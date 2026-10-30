// civ5headless.exe: headless Civilization V host for the Vox Populi game DLL.
//
//   civ5headless.exe boot --game-dir <path> --dll <path> [--db <path>] [--loc-db <path>]
//                         --out <run dir> [--strict] [options]
//   civ5headless.exe newgame --spec <resolved.json> --out <run dir> [--dll <path>]
//                         [--game-dir <path>] [--mods-dir <path>] [options]
//   civ5headless.exe loadgame --load <file.Civ5Save> --out <run dir> [options as newgame]
//
// "loadgame": boots as newgame does, loads a saved game (the real game's or the host's) and continues it
// (setup/HHSaveGame.h). Both save the last turn (unless --no-save-final) and, on request, more (--autosave,
// --save-at), always into <run>\saves.
//
// M2 "newgame": boots as "boot" does, applies a resolved game spec to pregame, starts the
// game as the real game would (map generation by the DLL and the map script) and writes the
// T000 outputs (t000-map.json, checkpoints/turn-0000.json, t000-digest.json).
//
// M1 "boot": loads the Firaxis support DLLs from the game folder and the game DLL under
// test, hands it the host's engine services, opens per-run copies of the game-built
// databases, initialises localization, caches the whole game database in the DLL,
// registers the Lua environment, runs a self-test and shuts down. See
// docs/headless-host-plan.md (M1) and docs/headless-host-ledger.md.
#include "HHCommon.h"
#include "engine/HHRuntime.h"
#include "engine/HHLogFiles.h"
#include "engine/HHEngineServices.h"
#include "lua/HHScriptSystem.h"
#include "lua/HHLuaEnv.h"
#include "setup/HHDatabase.h"
#include "setup/HHJson.h"
#include "setup/HHVfs.h"
#include "setup/HHNewGame.h"
#include "lua/HHLuaGlobals.h"
#include "setup/HHDbBuilder.h"
#include "setup/HHProfiles.h"
#include "setup/HHAddins.h"
#include "setup/HHSaveGame.h"
#include "engine/HHSha256.h"
#include <float.h>
#include <psapi.h>

// From shlobj.h, which does not compile after the Firaxis Win32 header's NO* switches.
extern "C" __declspec(dllimport) HRESULT __stdcall SHGetFolderPathW(HWND hwnd, int csidl, HANDLE hToken, DWORD dwFlags, LPWSTR pszPath);
const int HH_CSIDL_PERSONAL = 0x0005;

void HHExportsInit();
void HHExportsCheck();

namespace
{
	const char* kUsage =
		"usage: civ5headless.exe boot --game-dir <path> --dll <path> --out <run dir> [options]\n"
		"       civ5headless.exe newgame --spec <resolved.json> --out <run dir> [--dll <path>] [--game-dir <path>]\n"
		"                        [--mods-dir <path>] [options]\n"
		"       civ5headless.exe loadgame --load <file.Civ5Save> --out <run dir> [options as newgame]\n"
		"  --spec <path>        resolved game spec (python work/headless/gamespec.py resolve SPEC --out FILE)\n"
		"  --load <path>        loadgame: the saved game to continue (the real game's or the host's .Civ5Save); the activated\n"
		"                       mods and the DLL must be the save's (c5h run --load sets them up)\n"
		"  --reseed <map:sync>  loadgame: new seeds after the load (either side may be empty: the save's stays); the map is\n"
		"                       the save's, what is rolled from here on changes. VP rolls from the map seed (docs/saves.md)\n"
		"  --autosave <mode>    none (default), game (the game's own setting: UserSettings.ini TurnsBetweenAutosave) or n\n"
		"                       (every n turns): saves at the engine's autosave points, named as the game names them, in\n"
		"                       <save dir>\\auto; all are kept (as the real game keeps them in autoplay)\n"
		"  --save-at <turn>     newgame/loadgame: save at the autosave point of this turn (repeatable): Turn_NNNN <date>\n"
		"  --no-save-final      newgame/loadgame: no save once autoplay has ended (by default the last turn is saved,\n"
		"                       Final_NNNN <date>). Every save goes to <run>\\saves\n"
		"  --game-dir <path>    game folder (default: found through the registry: Civ V's Steam uninstall entry, then\n"
		"                       the Steam libraries)\n"
		"  --dll <path>         game DLL (newgame default: CvGameCore_Expansion2.dll in MODS\\(1) Community Patch)\n"
		"  --mods-dir <path>    deployed MODS folder, read only (default: <Documents>\\My Games\\...\\MODS)\n"
		"  --mods-db <path>     mods database to copy (default: <Documents>\\My Games\\...\\cache\\Civ5ModsDatabase.db)\n"
		"  --no-mods            no activated mods (no mod files in the VFS, IsModActivated false)\n"
		"  --no-lua-debug       remove the debug library from Lua's globals\n"
		"  --exe-seed <n>       the engine's own pregame seeds before Game.Init (random civs and city-states);\n"
		"                       a real run's HHSEEDS exeMap reproduces its choice (default: the spec's map seed);\n"
		"                       a DLL that pins the seeds at setGameStarted uses the spec's map seed instead\n"
		"  --crt-srand <n>      srand(n) before the new game: Lua's math.random state for map scripts that use it\n"
		"  --player-option <TYPE=0|1>  engine user setting for a player option (repeatable; default: database defaults\n"
		"                       plus the reference profile's PLAYEROPTION_SAFE_AUTOMATION=1)\n"
		"  --db <path>          game database to copy (default: <Documents>\\My Games\\Sid Meier's\n"
		"                       Civilization 5\\cache\\Civ5DebugDatabase.db)\n"
		"  --loc-db <path>      localization database to copy (default: Localization-Merged.db\n"
		"                       next to the default game database)\n"
		"  --icu <path>         ICU data (default: <game>\\Locale\\icudt44l.dat)\n"
		"  --language <type>    localization language (default: en_US)\n"
		"  --lua-path <dir>     folder LoadFile/include search (repeatable; default <game>\\Assets)\n"
		"  --mod <guid[:ver]>   activated mod for IsModActivated (repeatable)\n"
		"  --dlc <guid>         valid DLC for IsDLCValid (repeatable; default: every DLC valid)\n"
		"  --game-logging       ICvGameContext1::SetLogging(true) (the DLL's own logs)\n"
		"  --no-game-logging    newgame: no DLL logs (default: the reference profile's logging; older DLLs' AI results depend on it)\n"
		"  --turns <n>          newgame: autoplay n turns after T000 (ARM + CONTINUE as game_probe), then dump-t<n>;\n"
		"                       loadgame: n turns after the save's turn\n"
		"  --idle-updates <k>   newgame: Updates after autoplay ends, in the human turn (default 10)\n"
		"  --frame-skew <k>     newgame: an extra Update (no UI frame) before every k-th one; results must not change\n"
		"  --dump-turns <a:b>   newgame: the DLL dumps the state at doTurnBegin of turns a..b (Logs\\HeadlessDumps)\n"
		"  --no-dumps           newgame: no state dumps (dump-t000, dump-t<n>); digests are unaffected\n"
		"  --keep-log <glob>    write only the logs whose names match (* and ?, any case; repeatable): the DLL logs as\n"
		"                       before, the other logs are discarded unwritten (the logs the DLL writes itself, such as\n"
		"                       HeadlessDigest.log, are not affected)\n"
		"  --no-ui-frame        newgame: no UI frame emulation after each Update (civ ribbon, notification aging,\n"
		"                       popups, human-turn unit cycle)\n"
		"  --lua-before-dump <file>  newgame: run a Lua file after autoplay, before the final dump; its result goes to\n"
		"                       lua-before-dump.json\n"
		"  --lua-before-arm <file>   newgame: run a Lua file after the T000 outputs, before autoplay is armed (e.g.\n"
		"                       Game.SetStackingDiagnosticsLevel); its result goes to lua-before-arm.json\n"
		"  --checkpoint-lua <file>   newgame: run a Lua file whenever the game turn advances (every n turns, see\n"
		"                       --checkpoint-every) and once after autoplay; results go to checkpoints\\turn-NNNN.json\n"
		"                       and turn-NNNN-final.json in vpbench's format ({utc, seconds, data})\n"
		"  --checkpoint-every <n>    newgame: checkpoint turns that are multiples of n (default 1)\n"
		"  --crt-sse2 <0|1>     _set_SSE2_enable in every loaded MSVCR90 instance (experiment)\n"
		"  --heap-perturb <seed> address-perturbation self-test (results must not change): random heap spacer\n"
		"                       allocations interleaved with the DLL's allocations, and the game DLL rebased\n"
		"  --sba                a real small-block allocator behind FSBAAllocate (blocks up to 512 bytes), as the real\n"
		"                       exe has; off: every block from the CRT heap. Long games use a little less CPU\n"
		"  --known-sha256 <path=hex>  this file's SHA-256 for the reports, so the host need not read it (repeatable)\n"
		"  --profiles <dir>     newgame: UI emulation profiles (default: profiles\\ next to the exe or above it)\n"
		"  --no-profiles        newgame: no UI emulation profiles\n"
		"  --skip-addin <file>  newgame: do not run this InGameUIAddin (file name, with or without extension; repeatable)\n"
		"  --no-addins          newgame: run no InGameUIAddin\n"
		"  --lua-probe <file>   newgame: run a Lua file in an InGame context after the T000 outputs, before autoplay is\n"
		"                       armed (repeatable, in order, one shared context; e.g. probes\\fingerprint.lua)\n"
		"  --pregame-turn-timer <id>  newgame: PreGame turn timer after setGameSpeed (the real exe keeps the previous\n"
		"                       game's value for Marathon, which sets none)\n"
		"  --game-log-flags <m> with game logging: 1 AILog, 2 AIPerfLog, 4 BuilderAILog, 8 PlayerAndCityAILogSplit\n"
		"                       (default 15, as the reference profile; diagnostics of log-dependent results)\n"
		"  --rand-log <flags>   config.ini RandLog: 5 = every synced RNG call incl. map generation (Logs\\RandCalls.csv)\n"
		"  --strict             abort on the first unimplemented engine call\n"
		"  --verbose            log the first call of every implemented engine method too\n"
		"  --keep-dll-loaded    skip FreeLibrary of the game DLL at shutdown\n"
		"Every newgame run writes <run>\\host-report.json (docs/engine-contract.md, run report).\n";

	struct InfoCount
	{
		const char* szWhat;
		const char* szTable;   // table the DLL caches it from
		bool bCore;            // must match for the boot to pass
		int (DLLCALL ICvGameContext1::*pfnCount)();
	};

	const InfoCount kInfoCounts[] =
	{
		{ "Units",              "Units",                true,  &ICvGameContext1::GetNumUnitInfos },
		{ "Buildings",          "Buildings",            true,  &ICvGameContext1::GetNumBuildingInfos },
		{ "Technologies",       "Technologies",         true,  &ICvGameContext1::GetNumTechInfos },
		{ "Policies",           "Policies",             true,  &ICvGameContext1::GetNumPolicyInfos },
		{ "Civilizations",      "Civilizations",        true,  &ICvGameContext1::GetNumCivilizationInfos },
		{ "Promotions",         "UnitPromotions",       true,  &ICvGameContext1::GetNumPromotionInfos },
		{ "Improvements",       "Improvements",         true,  &ICvGameContext1::GetNumImprovementInfos },
		{ "Resources",          "Resources",            true,  &ICvGameContext1::GetNumResourceInfos },
		{ "Leaders",            "Leaders",              true,  &ICvGameContext1::GetNumLeaderHeadInfos },
		{ "Eras",               "Eras",                 true,  &ICvGameContext1::GetNumEraInfos },
		{ "Builds",             "Builds",               true,  &ICvGameContext1::GetNumBuildInfos },
		{ "MinorCivilizations", "MinorCivilizations",   true,  &ICvGameContext1::GetNumMinorCivInfos },
		{ "Terrains",           "Terrains",             true,  &ICvGameContext1::GetNumTerrainInfos },
		{ "Features",           "Features",             false, &ICvGameContext1::GetNumFeatureInfos },
		{ "Victories",          "Victories",            true,  &ICvGameContext1::GetNumVictoryInfos },
		{ "Worlds",             "Worlds",               true,  &ICvGameContext1::GetNumWorldInfos },
		{ "GameSpeeds",         "GameSpeeds",           true,  &ICvGameContext1::GetNumGameSpeedInfos },
		{ "Handicaps",          "HandicapInfos",        true,  &ICvGameContext1::GetNumHandicapInfos },
		{ "GameOptions",        "GameOptions",          false, &ICvGameContext1::GetNumGameOptionInfos },
		{ "Climates",           "Climates",             true,  &ICvGameContext1::GetNumClimateInfos },
		{ "SeaLevels",          "SeaLevels",            true,  &ICvGameContext1::GetNumSeaLevelInfos },
		{ "Colors",             "Colors",               true,  &ICvGameContext1::GetNumColorInfos },
		{ "PlayerColors",       "PlayerColors",         true,  &ICvGameContext1::GetNumPlayerColorInfos },
		{ "UnitCombatClasses",  "UnitCombatInfos",      true,  &ICvGameContext1::GetNumUnitCombatClassInfos },
	};

	struct CountResult
	{
		std::string what, table;
		int dll, sql;
		bool core;
	};

	std::string JsonEscape(const std::string& s)
	{
		std::string out;
		for(size_t i = 0; i < s.size(); ++i)
		{
			const unsigned char c = (unsigned char)s[i];
			switch(c)
			{
			case '"': out += "\\\""; break;
			case '\\': out += "\\\\"; break;
			case '\n': out += "\\n"; break;
			case '\r': out += "\\r"; break;
			case '\t': out += "\\t"; break;
			default:
				if(c < 0x20)
					out += HHFormat("\\u%04x", c);
				else
					out += (char)c;
			}
		}
		return out;
	}

	std::string DocumentsFolder()
	{
		wchar_t sz[MAX_PATH];
		if(SUCCEEDED(SHGetFolderPathW(NULL, HH_CSIDL_PERSONAL, NULL, 0, sz)))
			return HHNarrow(sz);
		return std::string();
	}

	std::string StripSlash(std::string s)
	{
		while(!s.empty() && (s[s.size() - 1] == '\\' || s[s.size() - 1] == '/'))
			s.erase(s.size() - 1);
		return s;
	}

	std::string RegString(HKEY hRoot, const wchar_t* szKey, const wchar_t* szValue, REGSAM eView)
	{
		HKEY h;
		if(RegOpenKeyExW(hRoot, szKey, 0, KEY_READ | eView, &h) != ERROR_SUCCESS)
			return std::string();
		wchar_t buf[2048];
		DWORD cb = sizeof(buf) - sizeof(wchar_t);
		DWORD type = 0;
		std::string out;
		if(RegQueryValueExW(h, szValue, NULL, &type, (LPBYTE)buf, &cb) == ERROR_SUCCESS && (type == REG_SZ || type == REG_EXPAND_SZ))
		{
			buf[cb / sizeof(wchar_t)] = 0;
			out = HHNarrow(buf);
		}
		RegCloseKey(h);
		return out;
	}

	bool IsGameDir(const std::string& dir)
	{
		return !dir.empty() && HHFileExists(dir + "\\CvGameDatabaseWin32Final Release.dll");
	}

	// The game folder without --game-dir: Civ V's Steam uninstall entry (app 8930), then every Steam library
	// (the Steam folder and the libraries of steamapps\libraryfolders.vdf). Empty when none has the game.
	std::string DiscoverGameDir(std::string& strSource)
	{
		const wchar_t* kUninstall = L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Steam App 8930";
		const REGSAM views[] = { KEY_WOW64_64KEY, KEY_WOW64_32KEY };
		for(int i = 0; i < 2; ++i)
		{
			const std::string dir = StripSlash(RegString(HKEY_LOCAL_MACHINE, kUninstall, L"InstallLocation", views[i]));
			if(IsGameDir(dir))
			{
				strSource = "registry: Steam App 8930 InstallLocation";
				return dir;
			}
		}
		std::vector<std::string> steams;
		steams.push_back(RegString(HKEY_CURRENT_USER, L"Software\\Valve\\Steam", L"SteamPath", 0));
		steams.push_back(RegString(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Valve\\Steam", L"InstallPath", KEY_WOW64_32KEY));
		steams.push_back(RegString(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Valve\\Steam", L"InstallPath", KEY_WOW64_64KEY));
		std::vector<std::string> libraries;
		for(size_t i = 0; i < steams.size(); ++i)
		{
			if(steams[i].empty())
				continue;
			const std::string steam = StripSlash(steams[i]);
			libraries.push_back(steam);
			// libraryfolders.vdf: "path" "D:\\SteamLibrary" (newer) or "1" "D:\\SteamLibrary" (older).
			std::string vdf;
			if(!HHReadTextFile(steam + "\\steamapps\\libraryfolders.vdf", vdf))
				continue;
			size_t pos = 0;
			while(pos < vdf.size())
			{
				size_t eol = vdf.find('\n', pos);
				if(eol == std::string::npos)
					eol = vdf.size();
				const std::string line = vdf.substr(pos, eol - pos);
				pos = eol + 1;
				std::vector<std::string> tokens;
				size_t q = 0;
				while((q = line.find('"', q)) != std::string::npos)
				{
					const size_t e = line.find('"', q + 1);
					if(e == std::string::npos)
						break;
					tokens.push_back(line.substr(q + 1, e - q - 1));
					q = e + 1;
				}
				if(tokens.size() != 2 || !(tokens[0] == "path" || (!tokens[0].empty() && isdigit((unsigned char)tokens[0][0]))))
					continue;
				std::string path;
				for(size_t k = 0; k < tokens[1].size(); ++k)
				{
					if(tokens[1][k] == '\\' && k + 1 < tokens[1].size() && tokens[1][k + 1] == '\\')
						++k;
					path += tokens[1][k];
				}
				libraries.push_back(StripSlash(path));
			}
		}
		for(size_t i = 0; i < libraries.size(); ++i)
		{
			const std::string dir = libraries[i] + "\\steamapps\\common\\Sid Meier's Civilization V";
			if(IsGameDir(dir))
			{
				strSource = "registry: Steam library " + libraries[i];
				return dir;
			}
		}
		return std::string();
	}

	// --autosave none | game | n. game: the game's own setting, UserSettings.ini [AutoSave] TurnsBetweenAutosave
	// (the game's default 10). Nothing is deleted: in autoplay the real game kept every autosave (seven with
	// NumAutosavesKept = 5; docs/saves.md).
	bool ParseAutosave(const std::string& v, HHOptions& o)
	{
		if(v == "none")
			o.autosaveTurns = 0;
		else if(v == "game")
		{
			const std::wstring ini = HHWiden(DocumentsFolder() + "\\My Games\\Sid Meier's Civilization 5\\UserSettings.ini");
			o.autosaveTurns = (int)GetPrivateProfileIntW(L"AutoSave", L"TurnsBetweenAutosave", 10, ini.c_str());
		}
		else
		{
			char* end = NULL;
			const long n = strtol(v.c_str(), &end, 10);
			if(v.empty() || *end || n <= 0)
				return false;
			o.autosaveTurns = (int)n;
		}
		return true;
	}

	// --reseed map:sync, a seed being a number that fits an unsigned int or empty (the save's).
	bool ParseSeed(const std::string& v, __int64& iSeed)
	{
		iSeed = -1;
		if(v.empty())
			return true;
		char* end = NULL;
		const __int64 n = _strtoi64(v.c_str(), &end, 10);
		if(*end || n < 0 || n > 0xFFFFFFFFLL)
			return false;
		iSeed = n;
		return true;
	}

	bool ParseReseed(const std::string& v, HHOptions& o)
	{
		const size_t colon = v.find(':');
		return colon != std::string::npos && ParseSeed(v.substr(0, colon), o.reseedMap) && ParseSeed(v.substr(colon + 1), o.reseedSync) &&
			(o.reseedMap >= 0 || o.reseedSync >= 0);
	}

	// --known-sha256 path=hex: the path can hold an equals sign, the hash cannot.
	bool ParseKnownHash(const std::string& v)
	{
		const size_t eq = v.rfind('=');
		if(eq == std::string::npos || eq == 0 || v.size() - eq - 1 != 64)
			return false;
		const std::string hex = v.substr(eq + 1);
		if(hex.find_first_not_of("0123456789ABCDEF") != std::string::npos)
			return false;
		HHSha256Known(v.substr(0, eq), hex);
		return true;
	}

	bool ParseArgs(int argc, wchar_t** argv, HHOptions& o)
	{
		if(argc < 2)
			return false;
		o.command = HHNarrow(argv[1]);
		for(int i = 2; i < argc; ++i)
		{
			const std::string a = HHNarrow(argv[i]);
			const bool bHasValue = i + 1 < argc;
			const std::string v = bHasValue ? HHNarrow(argv[i + 1]) : std::string();
			if(a == "--strict") o.strict = true;
			else if(a == "--verbose") o.verbose = true;
			else if(a == "--game-logging") o.gameLogging = true;
			else if(a == "--no-game-logging") o.noGameLogging = true;
			else if(a == "--no-ui-frame") o.noUiFrame = true;
			else if(a == "--keep-dll-loaded") o.freeLibrary = false;
			else if(a == "--no-mods") o.noMods = true;
			else if(a == "--no-lua-debug") o.luaDebugLibrary = false;
			else if(a == "--no-profiles") o.noProfiles = true;
			else if(a == "--no-addins") o.noAddins = true;
			else if(a == "--no-dumps") o.noDumps = true;
			else if(a == "--sba") o.sba = true;
			else if(a == "--save-final") o.saveFinal = true;
			else if(a == "--no-save-final") o.saveFinal = false;
			else if(!bHasValue) { fprintf(stderr, "missing value for %s\n", a.c_str()); return false; }
			else if(a == "--load") { o.loadPath = v; ++i; }
			else if(a == "--reseed")
			{
				if(!ParseReseed(v, o))
				{
					fprintf(stderr, "--reseed takes map:sync, numbers below 2^32 of which one may be empty, not %s\n", v.c_str());
					return false;
				}
				++i;
			}
			else if(a == "--known-sha256")
			{
				if(!ParseKnownHash(v))
				{
					fprintf(stderr, "--known-sha256 takes path=hex, 64 upper-case hex digits, not %s\n", v.c_str());
					return false;
				}
				++i;
			}
			else if(a == "--save-at") { o.saveAt.push_back(atoi(v.c_str())); ++i; }
			else if(a == "--autosave")
			{
				if(!ParseAutosave(v, o))
				{
					fprintf(stderr, "--autosave takes none, game or a number of turns, not %s\n", v.c_str());
					return false;
				}
				++i;
			}
			else if(a == "--profiles") { o.profilesDir = StripSlash(HHFullPath(v)); ++i; }
			else if(a == "--skip-addin") { o.skipAddins.push_back(v); ++i; }
			else if(a == "--keep-log") { o.keepLogs.push_back(v); ++i; }
			else if(a == "--lua-probe") { o.luaProbes.push_back(HHFullPath(v)); ++i; }
			else if(a == "--game-dir") { o.gameDir = StripSlash(v); ++i; }
			else if(a == "--dll") { o.dllPath = v; ++i; }
			else if(a == "--db") { o.dbPath = v; ++i; }
			else if(a == "--loc-db") { o.locDbPath = v; ++i; }
			else if(a == "--out") { o.outDir = StripSlash(v); ++i; }
			else if(a == "--icu") { o.icuPath = v; ++i; }
			else if(a == "--language") { o.language = v; ++i; }
			else if(a == "--lua-path") { o.luaSearch.push_back(StripSlash(HHFullPath(v))); ++i; }
			else if(a == "--dlc") { o.dlcs.push_back(v); ++i; }
			else if(a == "--spec") { o.specPath = v; ++i; }
			else if(a == "--mods-dir") { o.modsDir = StripSlash(v); ++i; }
			else if(a == "--mods-db") { o.modsDbPath = v; ++i; }
			else if(a == "--player-option") { o.playerOptions.push_back(v); ++i; }
			else if(a == "--exe-seed") { o.exeSeed = _strtoi64(v.c_str(), NULL, 10); ++i; }
			else if(a == "--dump-turns")
			{
				const size_t colon = v.find(':');
				o.dumpFrom = atoi(v.c_str());
				o.dumpTo = colon == std::string::npos ? o.dumpFrom : atoi(v.c_str() + colon + 1);
				++i;
			}
			else if(a == "--turns") { o.turns = atoi(v.c_str()); ++i; }
			else if(a == "--lua-before-dump") { o.luaBeforeDump = v; ++i; }
			else if(a == "--lua-before-arm") { o.luaBeforeArm = v; ++i; }
			else if(a == "--checkpoint-lua") { o.checkpointLua = v; ++i; }
			else if(a == "--checkpoint-every") { o.checkpointEvery = atoi(v.c_str()); ++i; }
			else if(a == "--rand-log") { o.randLog = atoi(v.c_str()); ++i; }
			else if(a == "--game-log-flags") { o.gameLogFlags = atoi(v.c_str()); ++i; }
			else if(a == "--pregame-turn-timer") { o.pregameTurnTimer = atoi(v.c_str()); ++i; }
			else if(a == "--heap-perturb") { o.heapPerturb = (unsigned int)strtoul(v.c_str(), NULL, 10); ++i; }
			else if(a == "--idle-updates") { o.idleUpdates = atoi(v.c_str()); ++i; }
			else if(a == "--frame-skew") { o.frameSkew = atoi(v.c_str()); ++i; }
			else if(a == "--crt-sse2") { o.crtSse2 = atoi(v.c_str()); ++i; }
			else if(a == "--fpu-precision") { o.fpuPrecision = atoi(v.c_str()); ++i; }
			else if(a == "--crt-srand") { o.crtSrand = _strtoi64(v.c_str(), NULL, 10); ++i; }
			else if(a == "--mod")
			{
				HHModRef m;
				size_t colon = v.find(':');
				m.id = v.substr(0, colon);
				m.version = colon == std::string::npos ? -1 : atoi(v.c_str() + colon + 1);
				o.mods.push_back(m);
				++i;
			}
			else { fprintf(stderr, "unknown option %s\n", a.c_str()); return false; }
		}
		if(o.command != "boot" && o.command != "newgame" && o.command != "loadgame")
		{
			fprintf(stderr, "unknown command %s\n", o.command.c_str());
			return false;
		}
		const std::string myGames = DocumentsFolder() + "\\My Games\\Sid Meier's Civilization 5\\";
		if(o.modsDir.empty()) o.modsDir = myGames + "MODS";
		if(o.modsDbPath.empty()) o.modsDbPath = myGames + "cache\\Civ5ModsDatabase.db";
		if(o.command == "newgame" || o.command == "loadgame")
		{
			if(o.command == "newgame" && o.specPath.empty())
			{
				fprintf(stderr, "newgame needs --spec\n");
				return false;
			}
			if(o.command == "loadgame" && o.loadPath.empty())
			{
				fprintf(stderr, "loadgame needs --load\n");
				return false;
			}
			if(o.command != "loadgame" && (o.reseedMap >= 0 || o.reseedSync >= 0))
			{
				fprintf(stderr, "--reseed is for loadgame; a new game takes its seeds from the spec\n");
				return false;
			}
			if(!o.specPath.empty())
				o.specPath = HHFullPath(o.specPath);
			if(!o.loadPath.empty())
				o.loadPath = HHFullPath(o.loadPath);
			// The reference profile's config.ini has LoggingEnabled, AILog, AIPerfLog, BuilderAILog and
			// PlayerAndCityAILogSplit on. DLLs before the logging fixes (GetBestSettlePlot, PlotPillageMoves) give
			// different AI results without AI logging, so newgame logs like the reference by default.
			if(!o.noGameLogging)
				o.gameLogging = true;
			if(o.dllPath.empty()) o.dllPath = o.modsDir + "\\(1) Community Patch\\CvGameCore_Expansion2.dll";
		}
		if(!o.gameDir.empty())
			o.gameDirSource = "--game-dir";
		else
		{
			o.gameDir = DiscoverGameDir(o.gameDirSource);
			if(o.gameDir.empty())
			{
				fprintf(stderr, "the game folder was not found in the registry; pass --game-dir\n");
				return false;
			}
		}
		if(o.dllPath.empty() || o.outDir.empty())
		{
			fprintf(stderr, "--dll and --out are required\n");
			return false;
		}
		o.modsDir = HHFullPath(o.modsDir);
		o.modsDbPath = HHFullPath(o.modsDbPath);
		o.gameDir = HHFullPath(o.gameDir);
		o.dllPath = HHFullPath(o.dllPath);
		o.outDir = HHFullPath(o.outDir);
		const std::string cache = DocumentsFolder() + "\\My Games\\Sid Meier's Civilization 5\\cache\\";
		if(o.dbPath.empty()) o.dbPath = cache + "Civ5DebugDatabase.db";
		if(o.locDbPath.empty()) o.locDbPath = cache + "Localization-Merged.db";
		if(o.icuPath.empty()) o.icuPath = o.gameDir + "\\Locale\\icudt44l.dat";
		o.dbPath = HHFullPath(o.dbPath);
		o.locDbPath = HHFullPath(o.locDbPath);
		o.icuPath = HHFullPath(o.icuPath);
		if(o.luaSearch.empty()) o.luaSearch.push_back(o.gameDir + "\\Assets");
		return true;
	}

	HMODULE LoadFiraxisDll(const std::string& strGameDir, const char* szName)
	{
		const std::string path = strGameDir + "\\" + szName;
		HMODULE h = LoadLibraryExW(HHWiden(path).c_str(), NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
		if(!h)
			HHLogError("cannot load %s (error %lu)", path.c_str(), GetLastError());
		else
			HHLog("loaded %s", path.c_str());
		return h;
	}

	std::string ModulePath(HMODULE h)
	{
		wchar_t sz[MAX_PATH];
		DWORD n = GetModuleFileNameW(h, sz, MAX_PATH);
		return n ? HHNarrow(std::wstring(sz, n)) : std::string("?");
	}

	struct BootState
	{
		std::string status;
		std::string dllVersion;
		std::string error;
		std::vector<CountResult> counts;
		HHSelfTestResult selfTest;
		std::vector<std::pair<std::string, double> > timings;
		bool bCacheOk, bPostOk, bCountsOk, bSelfTestRun;
		int iPlayableCivs, iAIPlayableCivs, iPlayableMinors;
		std::string crtModule;
		BootState() : bCacheOk(false), bPostOk(false), bCountsOk(false), bSelfTestRun(false),
			iPlayableCivs(-1), iAIPlayableCivs(-1), iPlayableMinors(-1) { selfTest.bPassed = false; }
	};

	void WriteReport(const BootState& s, const std::string& strPath)
	{
		const HHOptions& o = HHGetOptions();
		FILE* f = _wfopen(HHWiden(strPath).c_str(), L"w");
		if(!f)
			return;
		fprintf(f, "{\n");
		fprintf(f, "  \"status\": \"%s\",\n", JsonEscape(s.status).c_str());
		fprintf(f, "  \"error\": \"%s\",\n", JsonEscape(s.error).c_str());
		fprintf(f, "  \"dll\": \"%s\",\n", JsonEscape(o.dllPath).c_str());
		fprintf(f, "  \"dll_version\": \"%s\",\n", JsonEscape(s.dllVersion).c_str());
		fprintf(f, "  \"game_dir\": \"%s\",\n", JsonEscape(o.gameDir).c_str());
		fprintf(f, "  \"db_source\": \"%s\",\n", JsonEscape(o.dbPath).c_str());
		fprintf(f, "  \"loc_db_source\": \"%s\",\n", JsonEscape(o.locDbPath).c_str());
		fprintf(f, "  \"crt_module\": \"%s\",\n", JsonEscape(s.crtModule).c_str());
		fprintf(f, "  \"strict\": %s,\n", o.strict ? "true" : "false");
		fprintf(f, "  \"post_processing_ok\": %s,\n", s.bPostOk ? "true" : "false");
		fprintf(f, "  \"cache_ok\": %s,\n", s.bCacheOk ? "true" : "false");
		fprintf(f, "  \"counts_ok\": %s,\n", s.bCountsOk ? "true" : "false");
		fprintf(f, "  \"playable\": {\"civs\": %d, \"ai_civs\": %d, \"minor_civs\": %d},\n",
		        s.iPlayableCivs, s.iAIPlayableCivs, s.iPlayableMinors);
		fprintf(f, "  \"counts\": [\n");
		for(size_t i = 0; i < s.counts.size(); ++i)
			fprintf(f, "    {\"what\": \"%s\", \"table\": \"%s\", \"dll\": %d, \"sql\": %d, \"core\": %s}%s\n",
			        s.counts[i].what.c_str(), s.counts[i].table.c_str(), s.counts[i].dll, s.counts[i].sql,
			        s.counts[i].core ? "true" : "false", i + 1 < s.counts.size() ? "," : "");
		fprintf(f, "  ],\n");
		fprintf(f, "  \"self_test\": {\"run\": %s, \"passed\": %s, \"checks\": [\n", s.bSelfTestRun ? "true" : "false",
		        s.selfTest.bPassed ? "true" : "false");
		for(size_t i = 0; i < s.selfTest.checks.size(); ++i)
			fprintf(f, "    \"%s\"%s\n", JsonEscape(s.selfTest.checks[i]).c_str(), i + 1 < s.selfTest.checks.size() ? "," : "");
		fprintf(f, "  ]},\n");
		fprintf(f, "  \"timings_seconds\": {");
		for(size_t i = 0; i < s.timings.size(); ++i)
			fprintf(f, "%s\"%s\": %.3f", i ? ", " : "", s.timings[i].first.c_str(), s.timings[i].second);
		fprintf(f, "},\n");
		std::vector<HHCallRecord> calls = HHGetCallRecords();
		struct ByOrder { bool operator()(const HHCallRecord& a, const HHCallRecord& b) const { return a.order < b.order; } };
		std::sort(calls.begin(), calls.end(), ByOrder());
		fprintf(f, "  \"engine_calls\": [\n");
		for(size_t i = 0; i < calls.size(); ++i)
			fprintf(f, "    {\"method\": \"%s\", \"stub\": %s, \"count\": %ld, \"first_phase\": \"%s\"}%s\n",
			        JsonEscape(calls[i].name).c_str(), calls[i].stub ? "true" : "false", calls[i].count,
			        calls[i].phase.c_str(), i + 1 < calls.size() ? "," : "");
		fprintf(f, "  ],\n");
		std::vector<HHExportRecord> exports = HHGetExportRecords();
		fprintf(f, "  \"exports\": [\n");
		for(size_t i = 0; i < exports.size(); ++i)
			fprintf(f, "    {\"name\": \"%s\", \"count\": %ld, \"first_phase\": \"%s\"}%s\n", exports[i].name, exports[i].count,
			        exports[i].phase.c_str(), i + 1 < exports.size() ? "," : "");
		fprintf(f, "  ]\n}\n");
		fclose(f);
	}

	// The save the run loaded (null: a new game) and the saves it wrote, for the reports.
	std::string LoadJson()
	{
		const HHLoadRecord* pLoad = HHGetLoadRecord();
		if(!pLoad)
			return "null";
		const HHSaveHeader& h = pLoad->header;
		std::string mods, warnings;
		for(size_t i = 0; i < h.mods.size(); ++i)
			mods += std::string(i ? ", " : "") + "{\"id\": \"" + HHJsonEscape(h.mods[i].id) + HHFormat("\", \"version\": %d, \"name\": \"", h.mods[i].version) +
				HHJsonEscape(h.mods[i].name) + "\"}";
		for(size_t i = 0; i < pLoad->warnings.size(); ++i)
			warnings += std::string(i ? ", " : "") + "\"" + HHJsonEscape(pLoad->warnings[i]) + "\"";
		// The seeds after the load, null for one the save kept.
		const std::string reseed = !pLoad->bReseeded ? "null" :
			"{\"map\": " + (pLoad->reseedMap >= 0 ? HHFormat("%I64d", pLoad->reseedMap) : std::string("null")) +
			", \"sync\": " + (pLoad->reseedSync >= 0 ? HHFormat("%I64d", pLoad->reseedSync) : std::string("null")) +
			(pLoad->reseedSync >= 0 ? HHFormat(", \"rngBefore\": %I64u, \"rngAfter\": %I64u", pLoad->rngBefore, pLoad->rngAfter) : std::string()) + "}";
		return "{\"path\": \"" + HHJsonEscape(pLoad->path) + HHFormat("\", \"turn\": %d, \"bodyBytes\": %u, \"seconds\": %.3f, ", h.turn, pLoad->bodyBytes,
		                                                               pLoad->seconds) +
			"\"reseed\": " + reseed + ", " +
			"\"civilization\": \"" + HHJsonEscape(h.civilization) + "\", \"handicap\": \"" + HHJsonEscape(h.handicap) + "\", \"gameSpeed\": \"" +
			HHJsonEscape(h.gameSpeed) + "\", \"worldSize\": \"" + HHJsonEscape(h.worldSize) + "\", \"mapScript\": \"" + HHJsonEscape(h.mapScript) +
			"\", \"gameVersion\": \"" + HHJsonEscape(h.gameVersion) + "\", \"dllGuid\": \"" + HHGuidString(h.dllGuid) + "\", \"dllVersion\": \"" +
			HHJsonEscape(h.dllVersion) + HHFormat("\", \"dlc\": %u, \"mods\": [", (unsigned)h.dlc.size()) + mods + "], \"warnings\": [" + warnings + "]}";
	}

	std::string SavesJson(const char* szIndent)
	{
		const std::vector<HHSaveRecord>& saves = HHGetSaveRecords();
		std::string out = "[";
		for(size_t i = 0; i < saves.size(); ++i)
			out += std::string(i ? "," : "") + "\n" + szIndent + "{\"kind\": \"" + saves[i].kind + "\", \"turn\": " + HHFormat("%d", saves[i].turn) +
				", \"path\": \"" + HHJsonEscape(saves[i].path) + HHFormat("\", \"bytes\": %u, \"bodyBytes\": %u, \"seconds\": %.3f, \"error\": \"",
				                                                          saves[i].bytes, saves[i].bodyBytes, saves[i].seconds) +
				HHJsonEscape(saves[i].error) + "\"}";
		return out + (saves.empty() ? "]" : "\n" + std::string(szIndent, strlen(szIndent) >= 2 ? strlen(szIndent) - 2 : 0) + "]");
	}

	// Adds a "newgame" member to the report WriteReport wrote (its last line is "}").
	void WriteNewGameSection(const HHNewGameResult& ng, int iLuaErrors, const std::string& strPath)
	{
		std::string text;
		{
			FILE* f = _wfopen(HHWiden(strPath).c_str(), L"rb");
			if(!f)
				return;
			char buf[65536];
			size_t n;
			while((n = fread(buf, 1, sizeof(buf), f)) > 0)
				text.append(buf, n);
			fclose(f);
		}
		const size_t close = text.find_last_of('}');
		if(close == std::string::npos)
			return;
		const HHOptions& o = HHGetOptions();
		std::string add = ",\n  \"newgame\": {\n";
		add += HHFormat("    \"spec\": \"%s\",\n", HHJsonEscape(o.specPath).c_str());
		add += "    \"load\": " + LoadJson() + ",\n";
		add += "    \"saves\": " + SavesJson("      ") + ",\n";
		add += HHFormat("    \"mods_dir\": \"%s\",\n", HHJsonEscape(o.modsDir).c_str());
		add += HHFormat("    \"pregame_ok\": %s,\n    \"started\": %s,\n    \"outputs_ok\": %s,\n", ng.bPregameOk ? "true" : "false",
		                ng.bStarted ? "true" : "false", ng.bOutputsOk ? "true" : "false");
		add += HHFormat("    \"lua_errors\": %d,\n    \"map_width\": %d,\n    \"map_height\": %d,\n", iLuaErrors, ng.iWidth, ng.iHeight);
		add += HHFormat("    \"seed_mode\": \"%s\",\n    \"exe_seed\": %u,\n", ng.seedMode.c_str(), ng.uiExeSeed);
		add += HHFormat("    \"digest_total\": \"%s\",\n    \"error\": \"%s\",\n", ng.digestTotal.c_str(), HHJsonEscape(ng.error).c_str());
		add += "    \"timings_seconds\": {";
		for(size_t i = 0; i < ng.timings.size(); ++i)
			add += HHFormat("%s\"%s\": %.3f", i ? ", " : "", ng.timings[i].first.c_str(), ng.timings[i].second);
		add += "},\n    \"activated_mods\": [";
		for(size_t i = 0; i < HHGetVfs().GetMods().size(); ++i)
			add += HHFormat("%s\"%s\"", i ? ", " : "", HHJsonEscape(HHGetVfs().GetMods()[i].name).c_str());
		add += "],\n";
		if(o.turns > 0)
		{
			add += HHFormat("    \"autoplay\": {\"ok\": %s, \"start_turn\": %d, \"turns\": %d, \"updates\": %d, \"turn_seconds\": {", ng.bAutoplayOk ? "true" : "false",
			                ng.iStartTurn, ng.iTurnsPlayed, ng.iUpdates);
			for(size_t i = 0; i < ng.turnSeconds.size(); ++i)
				add += HHFormat("%s\"%d\": %.3f", i ? ", " : "", ng.turnSeconds[i].first, ng.turnSeconds[i].second);
			add += "}, \"turn_updates\": {";
			for(size_t i = 0; i < ng.turnUpdates.size(); ++i)
				add += HHFormat("%s\"%d\": %d", i ? ", " : "", ng.turnUpdates[i].first, ng.turnUpdates[i].second);
			add += "}, \"net_messages\": {";
			size_t k = 0;
			for(std::map<std::string, int>::const_iterator it = ng.netMessages.begin(); it != ng.netMessages.end(); ++it, ++k)
				add += HHFormat("%s\"%s\": %d", k ? ", " : "", it->first.c_str(), it->second);
			add += "}, \"addins\": [";
			for(size_t i = 0; i < ng.addins.size(); ++i)
				add += HHFormat("%s\"%s\"", i ? ", " : "", HHJsonEscape(ng.addins[i]).c_str());
			add += "]},\n";
		}
		add += "    \"steps\": [\n";
		for(size_t i = 0; i < ng.steps.size(); ++i)
			add += "      \"" + HHJsonEscape(ng.steps[i]) + "\"" + (i + 1 < ng.steps.size() ? ",\n" : "\n");
		add += "    ]\n  }\n}\n";
		std::string body = text.substr(0, close);
		while(!body.empty() && (body[body.size() - 1] == '\n' || body[body.size() - 1] == '\r' || body[body.size() - 1] == ' '))
			body.erase(body.size() - 1);
		HHWriteTextFile(strPath, body + add);
	}

	std::string Q(const std::string& s)
	{
		return "\"" + HHJsonEscape(s) + "\"";
	}

	std::string ExePath()
	{
		wchar_t sz[MAX_PATH];
		const DWORD n = GetModuleFileNameW(NULL, sz, MAX_PATH);
		return n ? HHNarrow(std::wstring(sz, n)) : std::string();
	}

	std::string FileBase(const std::string& path)
	{
		const size_t slash = path.find_last_of("\\/");
		return slash == std::string::npos ? path : path.substr(slash + 1);
	}

	// UI Lua files of the run that are not the base game's: InGameUIAddin contexts, mod and DLC files that override a
	// base-game UI file, and every Lua file of a DLC package that is not an official one (EUI and the like). A file is
	// known when a selected profile was selected by it (same hash) or known-ui-files.json lists it.
	std::string UIFilesJson(const HHNewGameResult& ng)
	{
		HHVfs& kVfs = HHGetVfs();
		HHProfiles& kProfiles = HHGetProfiles();
		std::vector<std::string> paths = kVfs.AllPaths();
		std::string unknown, known;
		int nKnown = 0, nUnknown = 0;
		std::set<std::string> seen;
		struct Item { std::string name, path, source, kind; };
		std::vector<Item> items;
		for(size_t i = 0; i < ng.uiAddins.size(); ++i)
			if(!ng.uiAddins[i].path.empty())
			{
				Item it = { FileBase(ng.uiAddins[i].path), ng.uiAddins[i].path, "addin:" + ng.uiAddins[i].mod, "addin" };
				items.push_back(it);
			}
		for(size_t i = 0; i < paths.size(); ++i)
		{
			const std::vector<HHVfsEntry>* pFound = kVfs.Find(FileBase(paths[i]));
			if(!pFound || pFound->empty())
				continue;
			const HHVfsEntry& e = pFound->front();
			if(e.tier == 0)
				continue;
			std::string dlcFolder;
			if(e.tier == 1)
			{
				dlcFolder = e.source.substr(4, e.source.find('/') == std::string::npos ? std::string::npos : e.source.find('/') - 4);
				if(kProfiles.IsOfficialDlc(dlcFolder))
					continue;
			}
			bool bOverridesUI = false;
			for(size_t k = 1; k < pFound->size(); ++k)
			{
				const std::string lower = HHLower((*pFound)[k].path);
				if(lower.find("\\ui\\") != std::string::npos && ((*pFound)[k].tier == 0 ||
				   ((*pFound)[k].tier == 1 && kProfiles.IsOfficialDlc((*pFound)[k].source.substr(4, (*pFound)[k].source.find('/') - 4)))))
					bOverridesUI = true;
			}
			if(!bOverridesUI && e.tier != 1)
				continue;
			Item it = { FileBase(e.path), e.path, e.source, bOverridesUI ? "override" : "dlc-ui" };
			items.push_back(it);
		}
		for(size_t i = 0; i < items.size(); ++i)
		{
			if(!seen.insert(HHLower(items[i].path)).second)
				continue;
			const std::string hash = HHSha256File(items[i].path);
			std::string by;
			const std::string json = "{\"name\": " + Q(items[i].name) + ", \"kind\": " + Q(items[i].kind) + ", \"source\": " + Q(items[i].source) +
				", \"path\": " + Q(items[i].path) + ", \"sha256\": " + Q(hash);
			if(kProfiles.IsKnownFile(items[i].name, hash, &by))
			{
				known += std::string(nKnown ? ",\n" : "\n") + "      " + json + ", \"knownBy\": " + Q(by) + "}";
				++nKnown;
			}
			else
			{
				unknown += std::string(nUnknown ? ",\n" : "\n") + "      " + json + "}";
				++nUnknown;
			}
		}
		return HHFormat("{\"knownListLoaded\": %s, \"known\": %d, \"unknownCount\": %d,\n    \"unknown\": [", kProfiles.HasKnownList() ? "true" : "false",
		                nKnown, nUnknown) + unknown + (nUnknown ? "\n    ]" : "]") + ",\n    \"knownFiles\": [" + known + (nKnown ? "\n    ]}" : "]}");
	}

	// <run>\host-report.json (plan 9.2): what ran, how, and everything a reader needs to judge the run.
	void WriteHostReport(const BootState& s, const HHNewGameResult& ng, int iLuaErrors, const std::string& strContextRecords,
	                     const HHScriptSystem* pScripts, double dTotalSeconds, const std::string& strPath)
	{
		const HHOptions& o = HHGetOptions();
		std::string r = "{\n  \"format\": \"civ5headless-host-report/1\",\n";
		r += "  \"status\": " + Q(s.status) + ",\n  \"error\": " + Q(s.error) + ",\n";
		// Host
		{
			const std::string exe = ExePath();
			std::string gitHead, gitDirty = "null";
			HHJson kBuild;
			std::string err;
			const size_t slash = exe.find_last_of('\\');
			if(slash != std::string::npos && HHJson::ParseFile(exe.substr(0, slash) + "\\build-result.json", kBuild, err))
			{
				gitHead = kBuild["git_head"].AsString();
				gitDirty = kBuild["git_dirty_paths"].Size() ? "true" : "false";
			}
			r += "  \"host\": {\"exe\": " + Q(exe) + ", \"sha256\": " + Q(HHSha256File(exe)) + ", \"gitHead\": " + Q(gitHead) +
				", \"gitDirty\": " + gitDirty + "},\n";
		}
		r += "  \"command\": " + Q(o.command) + ",\n  \"gameDir\": " + Q(o.gameDir) + ",\n  \"gameDirSource\": " + Q(o.gameDirSource) + ",\n";
		r += "  \"dll\": {\"path\": " + Q(o.dllPath) + ", \"sha256\": " + Q(HHSha256File(o.dllPath)) + ", \"version\": " + Q(s.dllVersion) +
			", \"digestAdditions\": " + (ng.digestTotal.empty() ? "false" : "true") + ", \"seedMode\": " + Q(ng.seedMode) +
			", \"hostChoseCivs\": " + (ng.hostChoseCivs ? "true" : "false") + "},\n";
		// City-states: every city-state slot after Init2 against the spec's pins (minorCivs). chosenBy: dll (the DLL
		// chose, pins only compared), spec (the host applied the pin), host (the host drew it).
		{
			std::string slots;
			int nPinned = 0, nMatching = 0;
			for(size_t i = 0; i < ng.minorCivs.size(); ++i)
			{
				const HHMinorCivSlot& m = ng.minorCivs[i];
				const bool bPinned = m.pinnedId >= 0, bMatch = bPinned && m.pinnedId == m.id;
				nPinned += bPinned ? 1 : 0;
				nMatching += bMatch ? 1 : 0;
				const char* szBy = !ng.hostChoseMinors ? "dll" : bMatch ? "spec" : "host";
				slots += std::string(i ? "," : "") + HHFormat("\n    {\"slot\": %d, \"id\": %d, \"type\": ", m.slot, m.id) + Q(m.type) +
					", \"chosenBy\": " + Q(szBy) + (bPinned ? HHFormat(", \"pinnedId\": %d, \"pinnedType\": ", m.pinnedId) + Q(m.pinnedType) +
					", \"match\": " + (bMatch ? "true" : "false") : std::string()) + "}";
			}
			r += "  \"minorCivs\": {\"chosenBy\": " + Q(ng.minorCivs.empty() ? "" : ng.hostChoseMinors ? "host" : "dll") +
				HHFormat(", \"pinned\": %d, \"matching\": %d, \"allMatch\": %s, \"slots\": [", nPinned, nMatching,
				         nPinned ? (nPinned == nMatching ? "true" : "false") : "null") + slots + (slots.empty() ? "]},\n" : "\n  ]},\n");
		}
		r += "  \"database\": {\"game\": " + Q(o.dbPath) + ", \"localization\": " + Q(o.locDbPath) + ", \"modsDb\": " + Q(o.noMods ? std::string() : o.modsDbPath) +
			", \"modsDir\": " + Q(o.modsDir) + "},\n";
		r += "  \"spec\": " + Q(o.specPath) + ",\n";
		r += "  \"load\": " + LoadJson() + ",\n";
		r += "  \"saves\": " + SavesJson("    ") + ",\n";
		r += HHFormat("  \"options\": {\"turns\": %d, \"heapPerturb\": %u, \"frameSkew\": %d, \"smallBlockAllocator\": %s, \"noUiFrame\": %s, \"gameLogging\": %s, \"gameLogFlags\": %d, "
		              "\"idleUpdates\": %d, \"noMods\": %s, \"noProfiles\": %s, \"noAddins\": %s, \"skipAddins\": [",
		              o.turns, o.heapPerturb, o.frameSkew, o.sba ? "true" : "false", o.noUiFrame ? "true" : "false", o.gameLogging ? "true" : "false", o.gameLogFlags,
		              o.idleUpdates, o.noMods ? "true" : "false", o.noProfiles ? "true" : "false", o.noAddins ? "true" : "false");
		for(size_t i = 0; i < o.skipAddins.size(); ++i)
			r += (i ? ", " : "") + Q(o.skipAddins[i]);
		r += "], \"luaProbes\": [";
		for(size_t i = 0; i < o.luaProbes.size(); ++i)
			r += (i ? ", " : "") + Q(o.luaProbes[i]);
		r += HHFormat("], \"autosaveTurns\": %d, \"saveFinal\": %s, \"saveAt\": [", o.autosaveTurns,
		              o.saveFinal ? "true" : "false");
		for(size_t i = 0; i < o.saveAt.size(); ++i)
			r += HHFormat("%s%d", i ? ", " : "", o.saveAt[i]);
		r += HHFormat("], \"noDumps\": %s, \"keepLogs\": [", o.noDumps ? "true" : "false");
		for(size_t i = 0; i < o.keepLogs.size(); ++i)
			r += (i ? ", " : "") + Q(o.keepLogs[i]);
		r += HHFormat("], \"logsDiscarded\": %u},\n", (unsigned)HHLogFileMgr::Get().GetDiscardedCount());
		// Mods
		r += "  \"mods\": [";
		for(size_t i = 0; i < HHGetVfs().GetMods().size(); ++i)
		{
			const HHActivatedMod& m = HHGetVfs().GetMods()[i];
			r += std::string(i ? "," : "") + "\n    {\"id\": " + Q(m.id) + HHFormat(", \"version\": %d", m.version) + ", \"name\": " + Q(m.name) +
				", \"folder\": " + Q(m.folder) + "}";
		}
		r += HHGetVfs().GetMods().empty() ? "],\n" : "\n  ],\n";
		// Profiles
		{
			const HHProfiles& kProfiles = HHGetProfiles();
			r += "  \"profiles\": {\"dir\": " + Q(kProfiles.GetDir()) + ", \"loadErrors\": [";
			for(size_t i = 0; i < kProfiles.GetLoadErrors().size(); ++i)
				r += (i ? ", " : "") + Q(kProfiles.GetLoadErrors()[i]);
			std::string sel, notSel;
			for(size_t i = 0; i < kProfiles.GetProfiles().size(); ++i)
			{
				const HHProfile& p = kProfiles.GetProfiles()[i];
				std::string item = "\n      {\"name\": " + Q(p.name) + ", \"hook\": " + Q(p.hook) + ", \"reason\": " + Q(p.reason);
				if(p.selected)
				{
					item += HHFormat(", \"calls\": %ld, \"failures\": %ld", p.calls, p.failures) + ", \"description\": " + Q(p.description) + "}";
					sel += (sel.empty() ? "" : ",") + item;
				}
				else
					notSel += (notSel.empty() ? "" : ",") + item + "}";
			}
			r += "],\n    \"selected\": [" + sel + (sel.empty() ? "]" : "\n    ]") + ",\n    \"notSelected\": [" + notSel + (notSel.empty() ? "]" : "\n    ]") + "},\n";
		}
		// UI add-ins
		{
			int nRun = 0, nFailed = 0, nSkipped = 0;
			std::string list;
			for(size_t i = 0; i < ng.uiAddins.size(); ++i)
			{
				const HHAddinContext& c = ng.uiAddins[i];
				nRun += c.status == "run";
				nFailed += c.status == "failed";
				nSkipped += c.status == "skipped";
				list += std::string(i ? "," : "") + "\n      {\"context\": " + Q(c.context) + ", \"mod\": " + Q(c.mod) + ", \"entry\": " + Q(c.entry) +
					", \"parent\": " + Q(c.parent) + ", \"status\": " + Q(c.status) + ", \"error\": " + Q(c.error) + ", \"hidden\": " +
					(c.hidden ? "true" : "false") + ", \"path\": " + Q(c.path) + ", \"sha256\": " + Q(c.sha256) + "}";
			}
			r += HHFormat("  \"addins\": {\"run\": %d, \"failed\": %d, \"skipped\": %d, \"contexts\": [", nRun, nFailed, nSkipped) + list +
				(list.empty() ? "]" : "\n    ]") + ",\n    \"listeners\": " + (strContextRecords.empty() ? std::string("{}") : strContextRecords) + "},\n";
		}
		r += "  \"uiFiles\": " + UIFilesJson(ng) + ",\n";
		// Engine calls the host only stubs
		{
			std::vector<HHCallRecord> calls = HHGetCallRecords();
			std::string list;
			int nUnknown = 0, n = 0;
			for(size_t i = 0; i < calls.size(); ++i)
			{
				if(!calls[i].stub)
					continue;
				std::string why;
				const bool bHarmless = HHIsHarmlessStub(calls[i].name, &why);
				nUnknown += !bHarmless;
				list += std::string(n++ ? "," : "") + "\n      {\"method\": " + Q(calls[i].name) + HHFormat(", \"count\": %ld", calls[i].count) +
					", \"firstPhase\": " + Q(calls[i].phase) + ", \"knownHarmless\": " + (bHarmless ? "true" : "false") + ", \"why\": " + Q(why) + "}";
			}
			r += HHFormat("  \"stubCalls\": {\"unknownCount\": %d, \"calls\": [", nUnknown) + list + (list.empty() ? "]}" : "\n    ]}") + ",\n";
		}
		// Lua errors
		{
			r += HHFormat("  \"luaErrors\": {\"failing\": %d, \"total\": %d, \"list\": [", iLuaErrors, pScripts ? pScripts->GetErrorTotal() : 0);
			if(pScripts)
				for(size_t i = 0; i < pScripts->GetErrorRecords().size(); ++i)
				{
					const HHLuaErrorRecord& e = pScripts->GetErrorRecords()[i];
					r += std::string(i ? "," : "") + "\n      {\"context\": " + Q(e.context) + ", \"file\": " + Q(e.file) + HHFormat(", \"line\": %d", e.line) +
						", \"message\": " + Q(e.message) + ", \"phase\": " + Q(e.phase) + ", \"counted\": " + (e.counted ? "true" : "false") + "}";
				}
			r += pScripts && !pScripts->GetErrorRecords().empty() ? "\n    ]},\n" : "]},\n";
		}
		// Turns, time, memory
		{
			double dAutoplay = 0.0;
			std::string perTurn;
			for(size_t i = 0; i < ng.turnSeconds.size(); ++i)
			{
				dAutoplay += ng.turnSeconds[i].second;
				perTurn += HHFormat("%s\"%d\": %.3f", i ? ", " : "", ng.turnSeconds[i].first, ng.turnSeconds[i].second);
			}
			r += HHFormat("  \"turns\": {\"played\": %d, \"updates\": %d, \"autoplaySeconds\": %.3f, \"secondsPerTurn\": {", ng.iTurnsPlayed, ng.iUpdates, dAutoplay) +
				perTurn + "}},\n";
			r += "  \"timings\": {";
			for(size_t i = 0; i < s.timings.size(); ++i)
				r += HHFormat("%s\"%s\": %.3f", i ? ", " : "", s.timings[i].first.c_str(), s.timings[i].second);
			r += HHFormat("},\n  \"wallSeconds\": %.3f,\n", dTotalSeconds);
			PROCESS_MEMORY_COUNTERS pmc;
			memset(&pmc, 0, sizeof(pmc));
			pmc.cb = sizeof(pmc);
			GetProcessMemoryInfo(GetCurrentProcess(), &pmc, sizeof(pmc));
			FILETIME c, e, k, u;
			double dCpu = 0.0;
			if(GetProcessTimes(GetCurrentProcess(), &c, &e, &k, &u))
				dCpu = ((double)(((unsigned __int64)k.dwHighDateTime << 32) | k.dwLowDateTime) +
				        (double)(((unsigned __int64)u.dwHighDateTime << 32) | u.dwLowDateTime)) / 1e7;
			r += HHFormat("  \"memory\": {\"peakWorkingSetMB\": %.1f, \"peakPrivateMB\": %.1f, \"cpuSeconds\": %.2f}\n}\n",
			              pmc.PeakWorkingSetSize / 1048576.0, pmc.PeakPagefileUsage / 1048576.0, dCpu);
		}
		HHWriteTextFile(strPath, r);
	}

	// Options for the game's start (new or loaded), experiments all: --crt-sse2, --fpu-precision, --crt-srand.
	void ApplyStartOptions(HHScriptSystem& kScripts)
	{
		const HHOptions& o = HHGetOptions();
		// Lua's math.random is the CRT rand() of the calling thread (MSVCR90, per-thread state,
		// seeded 1); map scripts that use it (Continental_Drift) depend on that state, which in
		// the real game is whatever the UI thread's earlier rand() calls left.
		if(o.crtSse2 >= 0)
		{
			// Every MSVCR90 instance in the process (the host's and the one lua51_Win32.dll uses can differ)
			HMODULE mods[512];
			DWORD cb = 0;
			EnumProcessModules(GetCurrentProcess(), mods, sizeof(mods), &cb);
			for(DWORD k = 0; k < cb / sizeof(HMODULE); ++k)
			{
				wchar_t name[MAX_PATH];
				if(!GetModuleFileNameW(mods[k], name, MAX_PATH))
					continue;
				typedef int (__cdecl *SetSse2)(int);
				SetSse2 pfn = (SetSse2)GetProcAddress(mods[k], "_set_SSE2_enable");
				if(pfn)
				{
					const int prev = pfn(o.crtSse2);
					HHLog("_set_SSE2_enable(%d) in %s: was %d", o.crtSse2, HHNarrow(name).c_str(), prev);
				}
			}
		}
		if(o.fpuPrecision)
		{
			unsigned int cw = 0;
			const unsigned int pc = o.fpuPrecision == 24 ? _PC_24 : o.fpuPrecision == 64 ? _PC_64 : _PC_53;
			_controlfp_s(&cw, pc, _MCW_PC);
			HHLog("x87 precision %d bits for the new game (--fpu-precision): control word %08x", o.fpuPrecision, cw);
		}
		if(o.crtSrand >= 0)
		{
			// lua51_Win32.dll has its own MSVCR90 instance: seed through Lua itself.
			const std::string chunk = HHFormat("math.randomseed(%u)", (unsigned int)o.crtSrand);
			if(lua_State* L = kScripts.CreateLuaThread("Headless randomseed"))
			{
				if(luaL_loadbuffer(L, chunk.c_str(), chunk.size(), "randomseed") == 0)
					kScripts.CallFunction(L, 0, 0);
				kScripts.FreeLuaThread(L);
			}
			HHLog("Lua math.randomseed(%u) (--crt-srand)", (unsigned int)o.crtSrand);
		}
	}

	// The listener registrations per context (HHLuaGlobals.cpp __HHContextRecords) as JSON; before Lua shuts down.
	std::string ContextRecordsJson(HHScriptSystem& kScripts)
	{
		std::string json;
		lua_State* L = kScripts.CreateLuaThread("Headless report");
		if(!L)
			return json;
		const char* szChunk = "return __HHContextRecords";
		if(luaL_loadbuffer(L, szChunk, strlen(szChunk), "=Headless report") == 0 && kScripts.CallFunction(L, 0, 1) && lua_istable(L, -1))
			json = HHLuaToJson(L, -1, 0);
		kScripts.FreeLuaThread(L);
		return json;
	}

	void LogCallSummary()
	{
		std::vector<HHCallRecord> calls = HHGetCallRecords();
		int nStub = 0, nImpl = 0;
		for(size_t i = 0; i < calls.size(); ++i)
			(calls[i].stub ? nStub : nImpl)++;
		HHLog("engine methods called: %d implemented, %d stubs", nImpl, nStub);
		for(size_t i = 0; i < calls.size(); ++i)
			HHLog("  %-5s %-60s x%-8ld (first in %s)", calls[i].stub ? "STUB" : "impl", calls[i].name.c_str(),
			      calls[i].count, calls[i].phase.c_str());
		std::vector<HHExportRecord> exports = HHGetExportRecords();
		HHLog("exports used:");
		for(size_t i = 0; i < exports.size(); ++i)
			HHLog("  %-40s x%-10ld %s", exports[i].name, exports[i].count, exports[i].count ? exports[i].phase.c_str() : "(unused)");
	}

	int Boot()
	{
		HHOptions& o = HHGetOptions();
		HHPaths& p = HHGetPaths();
		BootState s;
		s.status = "failed";
		double t0 = HHSeconds();
		double tPhase = t0;
		#define HH_TIMING(name) do { double _t = HHSeconds(); s.timings.push_back(std::make_pair(std::string(name), _t - tPhase)); tPhase = _t; } while(0)

		// Run folders. The working directory is the run folder: the DLL resolves its
		// crashlogs\ markers and minidumps relative to it.
		p.run = o.outDir + "\\";
		p.logs = p.run + "Logs\\";
		p.cache = p.run + "cache\\";
		p.db = p.run + "db\\";
		if(!HHEnsureDirectory(p.logs) || !HHEnsureDirectory(p.cache) || !HHEnsureDirectory(p.db) ||
		   !HHEnsureDirectory(p.run + "crashlogs"))
		{
			fprintf(stderr, "cannot create the run folder %s\n", p.run.c_str());
			return 2;
		}
		// Unattended: assertion failures are logged and auto-ignored instead of a dialog.
		FILE* pMarker = _wfopen(HHWiden(p.run + "crashlogs\\nopopups.please").c_str(), L"w");
		if(pMarker)
			fclose(pMarker);
		HHLogOpen(p.logs);
		HHLogFileMgr::Get().SetFolder(p.logs);
		HHLogFileMgr::Get().SetKeepPatterns(o.keepLogs);
		SetCurrentDirectoryW(HHWiden(p.run).c_str());
		// newgame and loadgame: newgame-report.json (its "newgame" member is the game's, whichever way it began).
		const bool bNewGame = o.command == "newgame";
		const bool bLoadGame = o.command == "loadgame";
		const bool bGame = bNewGame || bLoadGame;
		const std::string reportPath = p.run + (bGame ? "newgame-report.json" : "boot-report.json");

		HHLog("civ5headless boot");
		HHLog("  game dir  %s (%s)", o.gameDir.c_str(), o.gameDirSource.c_str());
		HHLog("  dll       %s", o.dllPath.c_str());
		HHLog("  db        %s", o.dbPath.c_str());
		HHLog("  loc db    %s", o.locDbPath.c_str());
		HHLog("  icu       %s", o.icuPath.c_str());
		HHLog("  run       %s", p.run.c_str());
		for(size_t i = 0; i < o.luaSearch.size(); ++i)
			HHLog("  lua path  %s", o.luaSearch[i].c_str());
		{
			unsigned int cw = 0;
			_controlfp_s(&cw, 0, 0);
			HHLog("  x87/SSE control word at start: %08x (game-core values come from the M0 trace)", cw);
		}

		// newgame: the spec first, so a bad spec fails before anything is loaded; loadgame: the save's header.
		HHJson kSpec;
		if(bLoadGame)
		{
			HHSaveHeader kHeader;
			std::string err;
			if(!HHReadSaveHeader(o.loadPath, kHeader, err))
			{
				s.error = "cannot load " + o.loadPath + ": " + err;
				HHLogError("%s", s.error.c_str());
				WriteReport(s, reportPath);
				return 2;
			}
			HHLog("  load      %s (turn %d, %u mod(s))", o.loadPath.c_str(), kHeader.turn, (unsigned)kHeader.mods.size());
		}
		if(bNewGame)
		{
			std::string err;
			if(!HHJson::ParseFile(o.specPath, kSpec, err))
			{
				s.error = "cannot read the spec " + o.specPath + ": " + err;
				HHLogError("%s", s.error.c_str());
				WriteReport(s, reportPath);
				return 2;
			}
			CopyFileW(HHWiden(o.specPath).c_str(), HHWiden(p.run + "spec.resolved.json").c_str(), FALSE);
			HHLog("  spec      %s", o.specPath.c_str());
		}

		HHSetPhase("load");
		HHExportsInit();
		SetDllDirectoryW(HHWiden(o.gameDir).c_str());
		// The host imports these three with delay loading, so they resolve from the game folder.
		if(!LoadFiraxisDll(o.gameDir, "CvGameDatabaseWin32Final Release.dll") ||
		   !LoadFiraxisDll(o.gameDir, "CvLocalizationWin32Final Release.dll") ||
		   !LoadFiraxisDll(o.gameDir, "lua51_Win32.dll"))
		{
			s.error = "cannot load the Firaxis DLLs from the game folder";
			WriteReport(s, reportPath);
			return 2;
		}

		// Mods and the Lua VFS (H10), after the Firaxis database DLL is loaded: a per-run copy of the mods database (never opened in
		// place), the activated mods in activation order, and the file index for include.
		HHSetPhase("vfs");
		{
			std::string modsDbCopy;
			if(!o.noMods)
			{
				modsDbCopy = p.db + "Civ5ModsDatabase.db";
				if(!CopyFileW(HHWiden(o.modsDbPath).c_str(), HHWiden(modsDbCopy).c_str(), FALSE))
				{
					s.error = HHFormat("cannot copy the mods database %s (error %lu)", o.modsDbPath.c_str(), GetLastError());
					HHLogError("%s", s.error.c_str());
					WriteReport(s, reportPath);
					return 2;
				}
				HHLog("copied %s -> %s", o.modsDbPath.c_str(), modsDbCopy.c_str());
			}
			HHVfs& kVfs = HHGetVfs();
			if(!kVfs.Build(o.gameDir, modsDbCopy, o.modsDir))
			{
				s.error = "cannot build the VFS (mods database unreadable)";
				WriteReport(s, reportPath);
				return 2;
			}
			kVfs.WriteIndexLog(p.logs + "vfs-index.log");
			// The live cache database has been seen with no activated mods while the game was
			// running (the mods browser rewrites it); a modded run must not silently go vanilla.
			if(!o.noMods && kVfs.GetMods().empty())
			{
				s.error = "the mods database copy has no activated mods (is the game running?); use --mods-db <snapshot> or --no-mods";
				HHLogError("%s", s.error.c_str());
				WriteReport(s, reportPath);
				return 2;
			}
			// IsModActivated answers from the activated mods unless --mod lists them explicitly.
			if(o.mods.empty())
				for(size_t i = 0; i < kVfs.GetMods().size(); ++i)
				{
					HHModRef m;
					m.id = kVfs.GetMods()[i].id;
					m.version = kVfs.GetMods()[i].version;
					o.mods.push_back(m);
					HHLog("activated mod %u: %s v%d %s", (unsigned)i, m.id.c_str(), m.version, kVfs.GetMods()[i].name.c_str());
				}
		}
		HH_TIMING("vfs");
		// UI emulation profiles (selected once the game has started).
		if(bGame && !o.noProfiles)
		{
			if(o.profilesDir.empty())
				o.profilesDir = HHDefaultProfilesDir();
			HHGetProfiles().Load(o.profilesDir);
			HHLog("profiles: %s (%u)", o.profilesDir.empty() ? "(none found)" : o.profilesDir.c_str(), (unsigned)HHGetProfiles().GetProfiles().size());
			for(size_t i = 0; i < HHGetProfiles().GetLoadErrors().size(); ++i)
				HHLogError("profiles: %s", HHGetProfiles().GetLoadErrors()[i].c_str());
		}


		HHScriptSystem* pScripts = new HHScriptSystem();
		HHEngineUtility* pEngine = new HHEngineUtility(pScripts);
		HHUserInterface* pUI = new HHUserInterface();

		// --heap-perturb: map the game DLL somewhere else than usual (reserve its normal base and a
		// seed-sized block first), so global and static addresses differ too.
		if(o.heapPerturb)
		{
			HMODULE hProbe = LoadLibraryExW(HHWiden(o.dllPath).c_str(), NULL, DONT_RESOLVE_DLL_REFERENCES);
			void* pBase = hProbe;
			if(hProbe)
				FreeLibrary(hProbe);
			void* pBlock = pBase ? VirtualAlloc(pBase, 64 * 1024, MEM_RESERVE, PAGE_NOACCESS) : NULL;
			void* pExtra = VirtualAlloc(NULL, (SIZE_T)(1 + o.heapPerturb % 61) * 1024 * 1024, MEM_RESERVE, PAGE_NOACCESS);
			HHLog("heap-perturb %u: game DLL normal base %p (reserved: %s), extra reservation %p", o.heapPerturb, pBase, pBlock ? "yes" : "no", pExtra);
		}
		HMODULE hGame = LoadLibraryExW(HHWiden(o.dllPath).c_str(), NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
		if(o.heapPerturb)
			HHLog("heap-perturb: game DLL loaded at %p", (void*)hGame);
		if(!hGame)
		{
			s.error = HHFormat("cannot load the game DLL (error %lu)", GetLastError());
			HHLogError("%s", s.error.c_str());
			WriteReport(s, reportPath);
			return 2;
		}
		HHLog("loaded %s", ModulePath(hGame).c_str());
		s.crtModule = ModulePath(GetModuleHandleW(L"msvcr90.dll"));
		HHLog("CRT: %s", s.crtModule.c_str());
		typedef ICvGameContext1* (*PFNGetContext)();
		PFNGetContext pfnGetContext = (PFNGetContext)GetProcAddress(hGame, "DllGetGameContext");
		ICvGameContext1* pContext = pfnGetContext ? pfnGetContext() : NULL;
		if(!pContext)
		{
			s.error = "DllGetGameContext missing or returned NULL";
			HHLogError("%s", s.error.c_str());
			WriteReport(s, reportPath);
			return 2;
		}
		ICvGameContext2* pContext2 = pContext->QueryInterface<ICvGameContext2>();
		ICvGameContext3* pContext3 = pContext->QueryInterface<ICvGameContext3>();
		s.dllVersion = pContext->GetDLLVersion() ? pContext->GetDLLVersion() : "";
		HHLog("game context: %s, interfaces 1%s%s", s.dllVersion.c_str(), pContext2 ? ", 2" : "", pContext3 ? ", 3" : "");
		HH_TIMING("load");

		HHSetPhase("interfaces");
		pScripts->SetScriptUtility(pContext->GetScriptSystemUtility());
		if(!pContext->SetDLLIFace(pEngine))
		{
			s.error = "SetDLLIFace rejected the engine utility";
			HHLogError("%s", s.error.c_str());
			WriteReport(s, reportPath);
			return 2;
		}
		if(pContext2)
			pContext2->SetEngineUserInterface(pUI);
		else
			HHLog("warning: no ICvGameContext2, user interface not set");
		// Boot order of the exe (trace): SetLogging, SetAILogging, SetAIPerfLogging, SetBuilderAILogging,
		// SetPlayerAndCityAILogSplit, from config.ini (LoggingEnabled, AILog, AIPerfLog, BuilderAILog,
		// PlayerAndCityAILogSplit; RandLog = 0 is not sent).
		pContext->SetLogging(o.gameLogging);
		if(o.gameLogging)
		{
			pContext->SetAILogging((o.gameLogFlags & 1) != 0);
			pContext->SetAIPerfLogging((o.gameLogFlags & 2) != 0);
			pContext->SetBuilderAILogging((o.gameLogFlags & 4) != 0);
			pContext->SetPlayerAndCityAILogSplit((o.gameLogFlags & 8) != 0);
		}
		// config.ini RandLog (bit 0 on, 1 callstacks, 2 pregame calls, 3 async): Logs\RandCalls.csv
		if(o.randLog > 0)
			pContext->SetRandLogging(o.randLog);

		HHSetPhase("database");
		HHDatabases dbs;
		if(!dbs.OpenGameDatabase(o.dbPath))
		{
			s.error = "cannot open the game database copy";
			WriteReport(s, reportPath);
			return 2;
		}
		pContext->SetGameDatabase(dbs.GetGame());
		// The engine's player options (user settings): PlayerOptions defaults, then
		// --player-option TYPE=0|1 overrides.
		{
			Database::Results kOptions;
			if(dbs.GetGame()->Execute(kOptions, "SELECT ID, Type, \"Default\" FROM PlayerOptions ORDER BY ID"))
				while(kOptions.Step())
					pEngine->AddPlayerOption(kOptions.GetText(1), kOptions.GetInt(0), kOptions.GetInt(2) != 0);
			// The reference profile's user setting (contract 1.4: getPlayerOption answers true for
			// WAIT_END_TURN, the DB default, and SAFE_AUTOMATION, a user setting); --player-option overrides.
			pEngine->SetPlayerOptionValue("PLAYEROPTION_SAFE_AUTOMATION", true);
			for(size_t i = 0; i < o.playerOptions.size(); ++i)
			{
				const std::string& v = o.playerOptions[i];
				const size_t eq = v.find('=');
				const std::string type = v.substr(0, eq);
				const bool bValue = eq != std::string::npos && atoi(v.c_str() + eq + 1) != 0;
				if(!pEngine->SetPlayerOptionValue(type, bValue))
					HHLogError("--player-option %s: unknown option", v.c_str());
				else
					HHLog("player option %s = %d", type.c_str(), bValue ? 1 : 0);
			}
		}

		HHSetPhase("localization");
		if(!dbs.InitLocalization(o.locDbPath, o.icuPath, o.language))
		{
			s.error = "localization initialisation failed";
			WriteReport(s, reportPath);
			return 2;
		}
		HH_TIMING("database_and_localization");

		HHSetPhase("init");
		pContext->Init();
		// Trace S1: the exe sets its version string right after ICvGameContext1::Init.
		if(ICvPreGame1* pPreGame = pContext->GetPreGame())
			pPreGame->setVersionString(std::string("403694 FINAL_RELEASE"));
		HH_TIMING("init");

		ICvDLLDatabaseUtility1* pDbUtil = pContext->GetDatabaseLoadUtility();
		if(!pDbUtil)
		{
			s.error = "GetDatabaseLoadUtility returned NULL";
			HHLogError("%s", s.error.c_str());
			WriteReport(s, reportPath);
			return 2;
		}
		// Assumed order (plan M1, to be confirmed by the M0 trace): post-processing runs on the
		// loaded database first (ICvDLLDatabaseUtility1 documents it as running after mods are
		// applied and after the database is built or read from cache), then caching.
		HHSetPhase("post-processing");
		// Trace S1 (hh-m0-s1-continents): the exe calls FlushGameDatabaseData,
		// PerformDatabasePostProcessing, CacheGameDatabaseData after activating mods.
		pDbUtil->FlushGameDatabaseData();
		s.bPostOk = pDbUtil->PerformDatabasePostProcessing();
		HHLog("PerformDatabasePostProcessing: %s", s.bPostOk ? "ok" : "FAILED");
		HH_TIMING("post_processing");

		HHSetPhase("cache");
		s.bCacheOk = pDbUtil->CacheGameDatabaseData();
		HHLog("CacheGameDatabaseData: %s", s.bCacheOk ? "ok" : "FAILED");
		// The VP_LUAAPI thread ran inside CacheGameDatabaseData; later threads (Map Script, InGame)
		// see no debug library in the real game.
		pScripts->HideDebugLibrary();
		HH_TIMING("cache");
		HHExportsCheck();

		HHSetPhase("counts");
		s.bCountsOk = true;
		HHLog("%-20s %-22s %6s %6s", "info", "table", "dll", "sql");
		for(size_t i = 0; i < sizeof(kInfoCounts) / sizeof(kInfoCounts[0]); ++i)
		{
			const InfoCount& c = kInfoCounts[i];
			CountResult r;
			r.what = c.szWhat;
			r.table = c.szTable;
			r.core = c.bCore;
			r.dll = (pContext->*c.pfnCount)();
			r.sql = dbs.CountRows(c.szTable);
			// A database whose IDs have gaps (the stock DLL does not renumber them; VP's post-processing does)
			// gives info arrays of MAX(ID) + 1 entries.
			const bool bExact = r.dll == r.sql;
			const bool bMatch = bExact || r.dll == dbs.IdRange(c.szTable);
			if(!bMatch && r.core)
				s.bCountsOk = false;
			HHLog("%-20s %-22s %6d %6d %s", c.szWhat, c.szTable, r.dll, r.sql,
			      bExact ? "" : bMatch ? "(MAX(ID) + 1: IDs with gaps)" : (r.core ? "MISMATCH" : "differs (not core)"));
			s.counts.push_back(r);
		}
		s.iPlayableCivs = pContext->GetNumPlayableCivilizationInfos();
		s.iAIPlayableCivs = pContext->GetNumAIPlayableCivilizationInfos();
		s.iPlayableMinors = pContext->GetNumPlayableMinorCivs();
		HHLog("playable civilizations %d, AI-playable %d, playable minor civs %d", s.iPlayableCivs, s.iAIPlayableCivs, s.iPlayableMinors);

		HHSetPhase("lua");
		int iDllUnits = pContext->GetNumUnitInfos();
		const bool bGameData = HHLuaRegisterGameData(*pScripts, pContext->GetScriptSystemUtility(), *dbs.GetGame());
		// Engine globals for map and gameplay scripts (H7): Locale, Modding, Events, LuaEvents,
		// MapModData, PreGame (read-only), IncludeFileList.
		const bool bEngineGlobals = HHLuaRegisterEngineGlobals(*pScripts, HHGetVfs(), pContext->GetPreGame());
		if(!bEngineGlobals)
			HHLogError("engine Lua globals: registration failed");
		if(bGameData && !bGame)
		{
			s.bSelfTestRun = true;
			s.selfTest = HHLuaSelfTest(*pScripts, *dbs.GetGame(), iDllUnits);
		}
		else
			s.selfTest.bPassed = bGameData && bEngineGlobals;
		// Localization: the dictionary must answer like the text database.
		{
			const char* szKey = "TXT_KEY_UNIT_WARRIOR";
			Localization::String strText = Localization::Lookup(szKey);
			const char* szText = strText.toUTF8();
			std::string expected;
			Database::Results kText;
			if(dbs.GetLocalization() && dbs.GetLocalization()->Execute(kText, "SELECT Text FROM Language_en_US WHERE Tag = ?"))
			{
				kText.Bind(1, szKey);
				if(kText.Step() && kText.GetText(0))
					expected = kText.GetText(0);
			}
			const bool bOk = szText && !expected.empty() && expected == szText;
			const std::string line = HHFormat("Localization::Lookup(%s) = '%s'; Language_en_US '%s'", szKey, szText ? szText : "(null)", expected.c_str());
			s.selfTest.checks.push_back(std::string(bOk ? "ok: " : "FAIL: ") + line);
			if(!bOk)
				s.selfTest.bPassed = false;
			HHLog("  self-test %s %s", bOk ? "ok  " : "FAIL", line.c_str());
		}
		if(!HHAddinsSelfTest(s.selfTest.checks))
			s.selfTest.bPassed = false;
		HHLog("Lua self-test: %s", s.selfTest.bPassed ? "passed" : "FAILED");
		HH_TIMING("lua");

		// newgame (M2): pregame, start, T000 outputs.
		HHNewGameResult ng;
		HHNewGameInterfaces nif;
		nif.pContext = pContext;
		nif.pPreGame = pContext->GetPreGame();
		nif.pPreGame2 = nif.pPreGame ? nif.pPreGame->QueryInterface<ICvPreGame2>() : NULL;
		nif.pGame = pContext->GetGame();
		nif.pMap = pContext->GetMap();
		nif.pWBLoader = pContext->GetWorldBuilderMapLoader();
		nif.pScripts = pScripts;
		nif.pDbUtil = pDbUtil;
		nif.pEngine = pEngine;
		nif.pUI = pUI;
		nif.pDb = dbs.GetGame();
		nif.iMaxMajorCivs = HHQueryMaxMajorCivs(*dbs.GetGame());
		HHLog("MAX_MAJOR_CIVS (Defines): %d", nif.iMaxMajorCivs);
		pUI->SetGame(nif.pGame);
		pEngine->SetPreGame(nif.pPreGame);
		const int iLuaErrorsBefore = pScripts->GetErrorCount();
		if(bGame && s.bPostOk && s.bCacheOk)
		{
			bool bStarted = false;
			if(bLoadGame)
			{
				HHSetPhase("load-game");
				ApplyStartOptions(*pScripts);
				bStarted = HHLoadSavedGame(o.loadPath, nif, ng);
			}
			else
			{
				HHSetPhase("pregame");
				if(HHApplyPregame(kSpec, nif, ng))
				{
					HHSetPhase("start");
					ApplyStartOptions(*pScripts);
					bStarted = HHStartNewGame(kSpec, nif, ng);
				}
			}
			if(bStarted)
			{
				// The in-game UI as the game loads it: emulation profiles, the load screen (pause, load-screen
				// profiles), the InGameUIAddin contexts; then the T000 outputs (the reference dump-t000 point).
				HHSetSaveGame(&nif);
				HHSetPhase("ui");
				HHStartUI(nif, ng);
				HHSetPhase("t000");
				HHLoadScreen(nif, ng);
				HHSetPhase("addins");
				HHLoadUIAddinsStep(nif, ng);
				HHSetPhase("t000");
				HHWriteT000(kSpec, nif, ng);
				HHSetPhase("probes");
				HHRunLuaProbes(nif, ng);
				if(o.turns > 0)
				{
					HHSetPhase("autoplay");
					HHRunAutoplay(nif, ng, o.turns, o.idleUpdates);
				}
			}
			if(!ng.error.empty())
				HHLogError("%s: %s", o.command.c_str(), ng.error.c_str());
			for(size_t i = 0; i < ng.timings.size(); ++i)
				s.timings.push_back(ng.timings[i]);
			tPhase = HHSeconds();
		}
		const int iLuaErrors = pScripts->GetErrorCount() - iLuaErrorsBefore;
		const std::string contextRecords = bGame ? ContextRecordsJson(*pScripts) : std::string();

		// Shutdown order: Lua first (its __gc metamethods include DLL and database code), then
		// the game (newgame: pregame's started flag cleared first), the DLL's game data, the
		// database handles it holds, localization, the DLL itself, and the database
		// connections last.
		HHSetPhase("shutdown");
		pScripts->LogIncludeSummary();
		pScripts->Shutdown();
		HHSetSaveGame(NULL);
		if(bGame && ng.bPregameOk)
			HHEndNewGame(nif, ng);
		pDbUtil->Destroy();
		pContext->Uninit();
		pContext->SetGameDatabase(NULL);
		dbs.ShutdownLocalization();
		HHExportsCheck();
		if(o.freeLibrary)
		{
			pContext->SetDLLIFace(NULL);
			if(!FreeLibrary(hGame))
				HHLogError("FreeLibrary failed (error %lu)", GetLastError());
			else
				HHLog("game DLL unloaded");
		}
		dbs.Close();
		HH_TIMING("shutdown");
		s.timings.push_back(std::make_pair(std::string("total"), HHSeconds() - t0));

		bool bOk = s.bPostOk && s.bCacheOk && s.bCountsOk && s.selfTest.bPassed;
		if(bGame)
		{
			// A save the run was asked for and could not write fails the run.
			std::string saveError;
			for(size_t i = 0; i < HHGetSaveRecords().size() && saveError.empty(); ++i)
				if(!HHGetSaveRecords()[i].error.empty())
					saveError = "save failed: " + HHGetSaveRecords()[i].error;
			bOk = bOk && ng.bPregameOk && ng.bStarted && ng.bOutputsOk && iLuaErrors == 0 && saveError.empty();
			s.status = bOk ? "passed" : "failed";
			if(s.error.empty())
				s.error = !ng.error.empty() ? ng.error : iLuaErrors ? HHFormat("%d Lua error(s); see Logs\\Lua.log", iLuaErrors) : saveError;
		}
		else
			s.status = bOk ? "passed" : "failed";
		LogCallSummary();
		WriteReport(s, reportPath);
		if(bGame)
		{
			WriteNewGameSection(ng, iLuaErrors, reportPath);
			WriteHostReport(s, ng, iLuaErrors, contextRecords, pScripts, HHSeconds() - t0, p.run + "host-report.json");
		}
		HHLog("%s %s in %.2f s; report %s", o.command.c_str(), s.status.c_str(), HHSeconds() - t0, reportPath.c_str());
		HHLogFileMgr::Get().CloseAll();
		HHLogClose();
		// pEngine and pUI stay alive: the DLL may still reference them from static destructors.
		UNUSED_VARIABLE(pEngine);
		UNUSED_VARIABLE(pUI);
		return bOk ? 0 : 1;
		#undef HH_TIMING
	}
}

int wmain(int argc, wchar_t** argv)
{
	HHInstallCrashHandler();
	if(argc >= 2 && wcscmp(argv[1], L"builddb") == 0)
		return HHBuildDbMain(argc, argv);
	HHOptions& o = HHGetOptions();
	if(!ParseArgs(argc, argv, o))
	{
		fputs(kUsage, stderr);
		return 2;
	}
	return Boot();
}
