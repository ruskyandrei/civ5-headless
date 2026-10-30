// Host runtime: options, run folders, host log and the call registry that counts every
// engine-interface call the DLL makes (implemented methods and generated stubs).
#pragma once
#include "HHCommon.h"

struct HHModRef
{
	std::string id;   // mod GUID as the DLL passes it (case-insensitive compare)
	int version;      // -1: any version
};

struct HHOptions
{
	std::string command;
	std::string gameDir;   // game install folder (Firaxis DLLs, Assets, Locale)
	std::string dllPath;   // game DLL under test
	std::string dbPath;    // source game database (copied into the run folder)
	std::string locDbPath; // source localization database (copied into the run folder)
	std::string outDir;    // run folder
	std::string icuPath;   // ICU data file
	std::string language;  // localization language type
	bool strict;           // abort on the first unimplemented stub call
	bool verbose;          // echo every first call to stdout
	bool gameLogging;      // ICvGameContext1::SetLogging(true) + the AI log switches (the reference profile)
	bool noGameLogging;    // newgame: turn the reference profile's logging off
	bool freeLibrary;      // FreeLibrary the game DLL at shutdown
	std::vector<std::string> luaSearch; // folders LoadFile searches, in order
	std::vector<HHModRef> mods;          // activated mods (IsModActivated)
	std::vector<std::string> dlcs;       // valid DLC package GUIDs; empty: every DLC is valid
	// newgame (M2)
	std::string specPath;    // resolved game spec (work/headless/gamespec.py resolve)
	std::string modsDir;     // deployed MODS folder (read-only); mod paths are re-rooted to it
	std::string modsDbPath;  // Civ5ModsDatabase.db (copied into the run folder)
	bool noMods;             // ignore the mods database (no activated mods)
	bool luaDebugLibrary;    // keep the debug table in _G (see HHScriptSystem)
	std::vector<std::string> playerOptions; // TYPE=0|1 overrides of the PlayerOptions defaults
	__int64 exeSeed;         // pregame seeds before Game.Init (-1: the spec's map seed)
	int dumpFrom, dumpTo;    // newgame: dump the state at doTurnBegin of these turns (-1: none)
	int turns;               // newgame: autoplay turns after T000 (0: none)
	bool noUiFrame;            // newgame: skip the per-Update UI frame emulation (EUI civ ribbon)
	std::string luaBeforeDump; // newgame (experiment): Lua file run after autoplay, before the final dump
	std::string luaBeforeArm;  // newgame (M6 tooling): Lua file run before autoplay is armed (result: lua-before-arm.json)
	std::string checkpointLua; // newgame (M6 tooling): Lua file run at turn changes (checkpoints\turn-NNNN.json) and at the end
	int checkpointEvery;       // newgame: checkpoint every n turns (with --checkpoint-lua; default 1)
	int randLog;             // SetRandLogging flags (config.ini RandLog; 0: off)
	int gameLogFlags;        // with game logging: 1 AILog, 2 AIPerfLog, 4 BuilderAILog, 8 PlayerAndCityAILogSplit
	int pregameTurnTimer;    // newgame: PreGame turn timer after the speed (-1: as setGameSpeed leaves it)
	unsigned int heapPerturb; // address-perturbation self-test seed (0: off): heap spacers + game DLL rebase
	int idleUpdates;        // newgame: Updates after autoplay ends (the reference idles in the human turn)
	int frameSkew;          // newgame: an extra Update before every frameSkew-th one (0: none)
	int crtSse2;             // _set_SSE2_enable(v) in every loaded MSVCR90 instance (-1: untouched)
	int fpuPrecision;        // x87 precision bits for the new game (24, 53, 64; 0: untouched)
	__int64 crtSrand;        // srand() on the main thread before the new game (-1: untouched, CRT default 1)
	__int64 reseedMap;       // loadgame: map seed after the load (-1: the save's)
	__int64 reseedSync;      // loadgame: sync seed and game generator after the load (-1: the save's)
	bool sba;                // the small-block allocator (engine/HHExports.cpp); off: every block from the CRT heap
	std::string profilesDir;              // UI emulation profiles (default: HHDefaultProfilesDir)
	bool noProfiles;                      // no UI emulation profiles at all
	std::vector<std::string> skipAddins;  // InGameUIAddin files not to run (name with or without extension)
	bool noAddins;                        // no InGameUIAddin at all
	std::vector<std::string> luaProbes;   // Lua files run in an InGame context before autoplay (fingerprint probe)
	std::string gameDirSource;            // where gameDir came from (option, registry, ...)
	bool noDumps;                         // newgame: no Game.DumpHeadlessState dumps (dump-t000, dump-tN)
	std::vector<std::string> keepLogs;    // log names (wildcards) the log manager writes; empty: all. The DLL logs
	                                      // as before (same calls, same results); the other logs are discarded.
	// Saves (setup/HHSaveGame.h); they go to <run>\saves
	std::string loadPath;                 // loadgame: the .Civ5Save to load
	int autosaveTurns;                    // autosave every n turns at the engine's autosave points (0: none)
	std::vector<int> saveAt;              // save at the autosave point of these turns
	bool saveFinal;                       // save once autoplay has ended (default; --no-save-final: not)
	HHOptions() : language("en_US"), strict(false), verbose(false), gameLogging(false), noGameLogging(false), freeLibrary(true),
		noMods(false), luaDebugLibrary(true), exeSeed(-1), crtSrand(-1), reseedMap(-1), reseedSync(-1), sba(false), fpuPrecision(0), crtSse2(-1), turns(0), noUiFrame(false), randLog(0), gameLogFlags(15), heapPerturb(0), pregameTurnTimer(-1), idleUpdates(10), frameSkew(0), checkpointEvery(1), dumpFrom(-1), dumpTo(-1),
		noProfiles(false), noAddins(false), noDumps(false), autosaveTurns(0), saveFinal(true) {}
};

struct HHPaths
{
	std::string run;    // run folder, with trailing backslash
	std::string logs;   // <run>Logs\  .
	std::string cache;  // <run>cache\  (GetCacheFolderPath)
	std::string db;     // <run>db\  (per-run database copies)
};

HHOptions& HHGetOptions();
HHPaths& HHGetPaths();

// Run folder and logs.
bool HHEnsureDirectory(const std::string& strPath);
std::string HHFullPath(const std::string& strPath);
bool HHFileExists(const std::string& strPath);
std::string HHFormat(const char* szFormat, ...);
std::string HHFormatV(const char* szFormat, va_list args);
std::wstring HHWiden(const std::string& str, UINT uiCodePage = CP_UTF8);
std::string HHNarrow(const std::wstring& wstr, UINT uiCodePage = CP_UTF8);
std::string HHLower(const std::string& str);
void HHLogOpen(const std::string& strLogsDir);
void HHLogClose();
void HHFatal(const char* szFormat, ...);
double HHSeconds(); // seconds since process start

// Boot phase, recorded with the first call of every method.
void HHSetPhase(const char* szPhase);
const char* HHGetPhase();

// Call registry.
struct HHCallRecord
{
	std::string name;
	bool stub;
	long count;
	int order;          // order of first call
	std::string phase;  // boot phase of first call
};
std::vector<HHCallRecord> HHGetCallRecords();

// FireWorks export counters (engine/HHExports.cpp).
struct HHExportRecord
{
	const char* name;
	long count;
	std::string phase;
};
std::vector<HHExportRecord> HHGetExportRecords();

// Engine methods the host only stubs that are known to be harmless (engine/HHStubPolicy.cpp); pWhy gets the reason.
bool HHIsHarmlessStub(const std::string& strMethod, std::string* pWhy);

// Logs fatal exceptions (HHCRASH lines: code, module+offset, registers, stack) (engine/HHCrash.cpp).
void HHInstallCrashHandler();
