#include "setup/HHNewGame.h"
#include "lua/HHScriptSystem.h"
#include "lua/HHLuaGlobals.h"
#include "engine/HHEngineServices.h"
#include "engine/HHRuntime.h"
#include "setup/HHVfs.h"
#include "setup/HHSaveGame.h"
#include "setup/HHAddins.h"
#include "setup/HHProfiles.h"

// Every engine-side call that changes DLL state is recorded in kResult.steps, so the run
// folder documents the exact sequence (newgame-report.json "steps").
#define HH_STEP(text, call) do { call; kResult.steps.push_back(text); } while(0)

int HHQueryMaxMajorCivs(Database::Connection& kDb)
{
	// The setup screen loops over GameDefines.MAX_MAJOR_CIVS (Defines). The DLL's MAX_MAJOR_CIVS is compiled in (22,
	// or 34, 43, 62 in DLLs built for more majors) and not readable through the interfaces, but its
	// PerformDatabasePostProcessing writes it into Defines (CvDllDatabaseUtility, InsertGameDefine): read after
	// post-processing, Defines has the loaded DLL's value.
	int iValue = -1;
	Database::Results kResults;
	if(kDb.Execute(kResults, "SELECT Value FROM Defines WHERE Name = 'MAX_MAJOR_CIVS'") && kResults.Step())
		iValue = kResults.GetInt(0);
	if(iValue <= 0 || iValue > MAX_CIV_PLAYERS)
	{
		HHLogError("Defines.MAX_MAJOR_CIVS is %d; using 22", iValue);
		iValue = 22;
	}
	return iValue;
}

namespace
{
	unsigned int SeedValue(const HHJson& j)
	{
		const double d = j.AsDouble(0.0);
		return (unsigned int)(__int64)d;
	}

	bool IsAbsolutePath(const std::string& s)
	{
		return (s.size() > 2 && s[1] == ':') || (s.size() > 1 && s[0] == '\\' && s[1] == '\\');
	}

	std::string Slashes(std::string s)
	{
		for(size_t i = 0; i < s.size(); ++i)
			if(s[i] == '/')
				s[i] = '\\';
		return s;
	}

	double Now() { return HHSeconds(); }

	// DLC the real game allows in single player (HHPREGAME dlcAllowed in S1 and S3, in this
	// order): the gameplay DLC and expansions, without DLC_SP_Maps and Tablet.
	const char* kDlcAllowed[] =
	{
		"293C1EE3-1176-44F6-AC1F-59663826DE74", // DLC_01 Mongols
		"B685D5DE-7CCA-4E75-81B4-2F60754E6330", // DLC_02 Spain and Inca
		"ECF7C605-BA11-4CAC-8D80-D71306AAC471", // DLC_03 Polynesia
		"B3030D39-C0D8-4BC7-91B1-7AD1CAF585AB", // DLC_04 Denmark
		"112C22B2-5308-42B6-B734-171CCAB3037B", // DLC_05 Korea
		"BBB0D085-A0B1-4475-B007-3E549CF3ADC3", // DLC_06 Ancient Wonders
		"EA67AED5-5859-4875-BF3A-360FE9E55D1B", // DLC_07 Civilization 5 Complete
		"7459BA32-5764-44AE-8E95-01AD0E0EFD48", // DLC_Deluxe Babylon
		"0E3751A1-F840-4E1B-9706-519BF484E59D", // Expansion (Gods and Kings)
		"6DA07636-4123-4018-B643-6575B4EC336B", // Expansion2 (Brave New World)
		"8871E748-29A4-4910-8C57-8C99E32D0167", // Shared (Upgrade 1)
	};

	bool ParseGuid(const char* sz, GUID& g)
	{
		unsigned int d1 = 0, d2 = 0, d3 = 0, b[8] = {0};
		if(sscanf(sz, "%8x-%4x-%4x-%2x%2x-%2x%2x%2x%2x%2x%2x", &d1, &d2, &d3, &b[0], &b[1], &b[2], &b[3], &b[4], &b[5], &b[6], &b[7]) != 11)
			return false;
		g.Data1 = d1;
		g.Data2 = (unsigned short)d2;
		g.Data3 = (unsigned short)d3;
		for(int i = 0; i < 8; ++i)
			g.Data4[i] = (unsigned char)b[i];
		return true;
	}
}

//------------------------------------------------------------------------------
// Task 1: pregame
//------------------------------------------------------------------------------
bool HHApplyPregame(const HHJson& kSpec, HHNewGameInterfaces& kIf, HHNewGameResult& kResult)
{
	ICvPreGame1* pg = kIf.pPreGame;
	const double t0 = Now();
	if(kSpec["specVersion"].AsInt(0) != 1 || !kSpec["map"].IsObject() || !kSpec["slots"].IsArray())
	{
		kResult.error = "not a resolved spec (specVersion 1 with map and slots); run work/headless/gamespec.py resolve";
		return false;
	}
	const HHJson& kMap = kSpec["map"];
	const std::string mapFile = kMap["file"].AsString();
	const bool bCiv5Map = kMap["kind"].AsString() == "civ5map";

	// --- A. Entering the single-player setup screen -------------------------------------
	// ASSUMPTION (trace): the exe starts the single-player setup from a reset pregame:
	// resetGame + resetPlayers, a single-player new game, the local player in slot 0
	// (taken, assigned). The setup screen's Lua then runs the sequence in B.
	HH_STEP("resetGame()", pg->resetGame());
	HH_STEP("resetPlayers()", pg->resetPlayers());
	// Trace S1/S3: the exe's per-slot setup load calls setPlayable(p, true) for every slot;
	// setWorldSize's slot reset later clears it for the slots beyond the world's default
	// (S3 dump-t000 pregame: playable 1 for the 6 active majors only).
	// The barbarian slot stays unplayable (S3 dump-t000 pregame: playable[63] = 0).
	for(int i = 0; i < MAX_CIV_PLAYERS; ++i)
		pg->setPlayable((PlayerTypes)i, true);
	kResult.steps.push_back(HHFormat("for i = 0..%d: setPlayable(i, true)", MAX_CIV_PLAYERS - 1));
	HH_STEP("setGameType(GAME_SINGLE_PLAYER, GAME_NEW)", pg->setGameType(GAME_SINGLE_PLAYER, GAME_NEW));
	// The engine's own seeds (trace S1/S3: map == sync, one clock value): pregame holds them
	// until CvGame::init pins the spec seeds. closeInactiveSlots draws the random civs and
	// city-states from them. --exe-seed sets it (a real run's HHSEEDS exeMap reproduces its
	// civ and city-state choice); the default is the spec's map seed.
	{
		const __int64 iExe = HHGetOptions().exeSeed;
		kResult.uiExeSeed = iExe >= 0 ? (unsigned int)iExe : SeedValue(kSpec["seeds"]["map"]);
		HH_STEP(HHFormat("setMapRandomSeed(%u)", kResult.uiExeSeed), pg->setMapRandomSeed(kResult.uiExeSeed));
		HH_STEP(HHFormat("setSyncRandomSeed(%u)", kResult.uiExeSeed), pg->setSyncRandomSeed(kResult.uiExeSeed));
	}
	HH_STEP("setActivePlayer(0)", pg->setActivePlayer((PlayerTypes)0));
	// Session values the real game has from its configuration (S1/S3 HHPREGAME): game name,
	// the local player's nickname and the allowed DLC.
	HH_STEP("setGameName(\"My Game\")", pg->setGameName(CvString("My Game")));
	HH_STEP("setNickname(0, \"Player 1\")", pg->setNickname((PlayerTypes)0, CvString("Player 1")));
	HH_STEP("clearDLCAllowed()", pg->clearDLCAllowed());
	for(size_t i = 0; i < sizeof(kDlcAllowed) / sizeof(kDlcAllowed[0]); ++i)
	{
		GUID g;
		if(ParseGuid(kDlcAllowed[i], g))
			pg->setDLCAllowed(g, true);
	}
	kResult.steps.push_back(HHFormat("setDLCAllowed(..., true) x%u", (unsigned)(sizeof(kDlcAllowed) / sizeof(kDlcAllowed[0]))));
	HH_STEP("setSlotStatus(0, SS_TAKEN)", pg->setSlotStatus((PlayerTypes)0, SS_TAKEN));
	HH_STEP("setSlotClaim(0, SLOTCLAIM_ASSIGNED)", pg->setSlotClaim((PlayerTypes)0, SLOTCLAIM_ASSIGNED));

	// --- B. The setup screen (gamespec.py pregame_lua, same order) ----------------------
	HH_STEP("setLeaderName(0, \"\")", pg->setLeaderName((PlayerTypes)0, CvString("")));
	HH_STEP("setCivilizationDescription(0, \"\")", pg->setCivilizationDescription((PlayerTypes)0, CvString("")));
	HH_STEP("setCivilizationShortDescription(0, \"\")", pg->setCivilizationShortDescription((PlayerTypes)0, CvString("")));
	HH_STEP("setCivilizationAdjective(0, \"\")", pg->setCivilizationAdjective((PlayerTypes)0, CvString("")));
	for(int i = 0; i < kIf.iMaxMajorCivs; ++i)
	{
		pg->setCivilization((PlayerTypes)i, NO_CIVILIZATION);
		pg->setTeamType((PlayerTypes)i, (TeamTypes)i);
	}
	kResult.steps.push_back(HHFormat("for i = 0..%d: setCivilization(i, -1); setTeamType(i, i)", kIf.iMaxMajorCivs - 1));

	const int iWorldSize = kSpec["worldSize"]["id"].AsInt(-1);
	HH_STEP("setRandomWorldSize(false)", pg->setRandomWorldSize(false));
	// setWorldSize resets the slots (CvPreGame::resetSlots): the world's default player count
	// become computer slots with a random civ, the rest are reset and closed.
	HH_STEP(HHFormat("setWorldSize(%d)", iWorldSize), pg->setWorldSize((WorldSizeTypes)iWorldSize));
	HH_STEP(HHFormat("setNumMinorCivs(%d)", kSpec["cityStates"].AsInt()), pg->setNumMinorCivs(kSpec["cityStates"].AsInt()));
	HH_STEP("setLoadWBScenario(false)", pg->setLoadWBScenario(false));
	HH_STEP("setRandomMapScript(false)", pg->setRandomMapScript(false));
	// The script name is stored exactly as the MapScripts / Map_Sizes FileName (absolute for
	// mod scripts, game-relative for base scripts and premade maps): the DLL looks up
	// MapScriptOptions defaults by this string, and LoadFile resolves it.
	HH_STEP(HHFormat("setMapScriptName(\"%s\")", mapFile.c_str()), pg->setMapScriptName(CvString(mapFile.c_str())));
	// gamespec.py pregame_lua sets GAMESPEED_STANDARD (ID 2, the DLL's hard-coded case for TURNTIMER_MEDIUM)
	// first, so Marathon, which sets no timer, gets a fixed one; mirrored here.
	HH_STEP("setGameSpeed(2 /* GAMESPEED_STANDARD: turn timer pin */)", pg->setGameSpeed((GameSpeedTypes)2));
	HH_STEP(HHFormat("setGameSpeed(%d)", kSpec["speed"]["id"].AsInt()), pg->setGameSpeed((GameSpeedTypes)kSpec["speed"]["id"].AsInt()));
	// PreGame state the spec does not set: the real exe keeps it from the previous game of the session
	// (setGameSpeed sets no turn timer for Marathon). --pregame-turn-timer reproduces such a reference.
	if(HHGetOptions().pregameTurnTimer >= 0)
		HH_STEP(HHFormat("setTurnTimer(%d)", HHGetOptions().pregameTurnTimer), pg->setTurnTimer((TurnTimerTypes)HHGetOptions().pregameTurnTimer));
	HH_STEP(HHFormat("setEra(%d)", kSpec["era"]["id"].AsInt()), pg->setEra((EraTypes)kSpec["era"]["id"].AsInt()));
	HH_STEP(HHFormat("setHandicap(0, %d)", kSpec["difficulty"]["id"].AsInt()),
	        pg->setHandicap((PlayerTypes)0, (HandicapTypes)kSpec["difficulty"]["id"].AsInt()));
	{
		const std::vector<std::pair<std::string, HHJson> >& v = kSpec["victories"].Members();
		for(size_t i = 0; i < v.size(); ++i)
		{
			const int id = v[i].second["id"].AsInt();
			const bool b = v[i].second["enabled"].AsBool(true);
			HH_STEP(HHFormat("setVictory(%d /* %s */, %s)", id, v[i].first.c_str(), b ? "true" : "false"), pg->setVictory((VictoryTypes)id, b));
		}
	}
	HH_STEP(HHFormat("setMaxTurns(%d)", kSpec["maxTurns"].AsInt()), pg->setMaxTurns(kSpec["maxTurns"].AsInt()));
	HH_STEP("ResetGameOptions()", pg->ResetGameOptions());
	HH_STEP("ResetMapOptions()", pg->ResetMapOptions());
	// Options: only those the spec sets; everything else stays unset (the DLL then reads
	// MapScriptOptions.DefaultValue on first use, as in the real game).
	{
		const std::vector<std::pair<std::string, HHJson> >& v = kSpec["gameOptions"].Members();
		for(size_t i = 0; i < v.size(); ++i)
		{
			// PreGame.SetGameOption(type, bool) in the setup screen: the value is 0/1.
			const int iValue = v[i].second["value"].AsInt() ? 1 : 0;
			HH_STEP(HHFormat("SetGameOption(\"%s\", %d)", v[i].first.c_str(), iValue), pg->SetGameOption(v[i].first.c_str(), iValue));
		}
	}
	{
		// Map options are keyed by OptionID ("1", "4", ...): Map.GetCustomOption(n) and the
		// setup screen's PreGame.SetMapOption(n, value) both pass the number as a string.
		const std::vector<std::pair<std::string, HHJson> >& v = kMap["options"].Members();
		for(size_t i = 0; i < v.size(); ++i)
		{
			const int iValue = v[i].second["value"].AsInt();
			HH_STEP(HHFormat("SetMapOption(\"%s\" /* %s */, %d)", v[i].first.c_str(), v[i].second["name"].AsString().c_str(), iValue),
			        pg->SetMapOption(v[i].first.c_str(), iValue));
		}
	}
	// Slots: status, claim, civilization, team.
	const HHJson& kSlots = kSpec["slots"];
	for(size_t i = 0; i < kSlots.Size(); ++i)
	{
		const HHJson& s = kSlots.At(i);
		const PlayerTypes p = (PlayerTypes)s["slot"].AsInt();
		const std::string status = s["status"].AsString();
		const SlotStatus eStatus = status == "taken" ? SS_TAKEN : (status == "computer" ? SS_COMPUTER : SS_CLOSED);
		const char* szStatus = eStatus == SS_TAKEN ? "SS_TAKEN" : (eStatus == SS_COMPUTER ? "SS_COMPUTER" : "SS_CLOSED");
		HH_STEP(HHFormat("setSlotStatus(%d, %s)", (int)p, szStatus), pg->setSlotStatus(p, eStatus));
		// Claims: closing a slot unassigns it; opening one keeps its claim, so only the world's
		// default slots (resetSlots in setWorldSize) are assigned. M5 references: majors2 has slots
		// 2-7 unassigned, majors12 (standard world, 8 default players) slots 8-11 unassigned.
		const bool bUnassign = eStatus == SS_CLOSED;
		const SlotClaim eClaim = bUnassign ? SLOTCLAIM_UNASSIGNED : pg->slotClaim(p);
		if(pg->slotClaim(p) != eClaim)
			HH_STEP(HHFormat("setSlotClaim(%d, %s)", (int)p, eClaim == SLOTCLAIM_ASSIGNED ? "SLOTCLAIM_ASSIGNED" : "SLOTCLAIM_UNASSIGNED"),
			        pg->setSlotClaim(p, eClaim));
	}
	for(size_t i = 0; i < kSlots.Size(); ++i)
	{
		const HHJson& s = kSlots.At(i);
		const PlayerTypes p = (PlayerTypes)s["slot"].AsInt();
		const int iCiv = s["civId"].AsInt(-1);
		if(iCiv >= 0)
			HH_STEP(HHFormat("setCivilization(%d, %d /* %s */)", (int)p, iCiv, s["civ"].AsString().c_str()),
			        pg->setCivilization(p, (CivilizationTypes)iCiv));
		const int iTeam = s["team"].AsInt((int)p);
		if(iTeam != (int)p)
			HH_STEP(HHFormat("setTeamType(%d, %d)", (int)p, iTeam), pg->setTeamType(p, (TeamTypes)iTeam));
	}

	// --- C. After the setup screen (trace S1/S3, hh-m0-s1-continents) ---------------------
	// vpbench: PreGame.SetPersistSettings(false), then Events.SerialEventStartGame(); the exe
	// then verifies the local player's handicap and re-sets the active player.
	HH_STEP("setPersistSettings(false)", pg->setPersistSettings(false));
	if(kIf.pPreGame2)
		HH_STEP("VerifyHandicap(0)", kIf.pPreGame2->VerifyHandicap((PlayerTypes)0));
	HH_STEP("setActivePlayer(0)", pg->setActivePlayer((PlayerTypes)0));
	// The exe round-trips pregame through a NetInitInfo (Create, Write; Create, Read, Commit).
	// The host commits one built from the current pregame: Commit re-applies the map script
	// (through ICvEngineUtility4::GetEvaluatedMapScriptPath), world size (no slot reset),
	// options, victories, seeds and the other session settings.
	{
		const unsigned int uiInfo = kIf.pContext->CreateNetInitInfo();
		ICvNetInitInfo1* pInfo = kIf.pContext->GetNetInitInfo(uiInfo);
		bool bCommitted = false;
		if(pInfo)
		{
			HH_STEP("NetInitInfo.Commit()", bCommitted = pInfo->Commit());
			pInfo->Destroy();
		}
		kIf.pContext->DestroyNetInitInfo(uiInfo);
		if(!bCommitted)
			HHLogError("NetInitInfo commit failed");
	}
	// The exe re-applies world size (without a slot reset), climate, sea level and era.
	HH_STEP(HHFormat("setWorldSize(%d, false)", (int)pg->worldSize()), pg->setWorldSize(pg->worldSize(), false));
	HH_STEP(HHFormat("setClimate(%d)", (int)pg->climate()), pg->setClimate(pg->climate()));
	HH_STEP(HHFormat("setSeaLevel(%d)", (int)pg->seaLevel()), pg->setSeaLevel(pg->seaLevel()));
	HH_STEP(HHFormat("setEra(%d)", (int)pg->era()), pg->setEra(pg->era()));
	// Already cached: a no-op unless the database was flushed (kept for the call order).
	if(kIf.pDbUtil)
		HH_STEP("CacheGameDatabaseData()", kIf.pDbUtil->CacheGameDatabaseData());

	// Checks.
	bool bOk = true;
	if(pg->worldSize() != (WorldSizeTypes)iWorldSize)
	{
		kResult.error = HHFormat("world size %d did not stick (pregame says %d)", iWorldSize, (int)pg->worldSize());
		bOk = false;
	}
	if(pg->isWBMapScript() != bCiv5Map)
	{
		kResult.error = HHFormat("isWBMapScript() = %d for map kind %s", pg->isWBMapScript() ? 1 : 0, kMap["kind"].AsString().c_str());
		bOk = false;
	}
	for(size_t i = 0; i < kSlots.Size(); ++i)
	{
		const HHJson& s = kSlots.At(i);
		const PlayerTypes p = (PlayerTypes)s["slot"].AsInt();
		const int iCiv = s["civId"].AsInt(-1);
		if(iCiv >= 0 && pg->civilization(p) != (CivilizationTypes)iCiv)
		{
			kResult.error = HHFormat("slot %d: civilization %d did not stick (%d)", (int)p, iCiv, (int)pg->civilization(p));
			bOk = false;
		}
	}
	kResult.timings.push_back(std::make_pair(std::string("pregame"), Now() - t0));
	kResult.bPregameOk = bOk;
	return bOk;
}

//------------------------------------------------------------------------------
// Task 4: start sequence
//------------------------------------------------------------------------------
namespace
{
	// True when the DLL implements seed pinning through <cache>\HeadlessSeeds.txt (hh-digest).
	bool DllHasSeedPinning(const std::string& strDll)
	{
		FILE* f = _wfopen(HHWiden(strDll).c_str(), L"rb");
		if(!f)
			return false;
		std::string data;
		char buf[1 << 16];
		size_t n;
		while((n = fread(buf, 1, sizeof(buf), f)) > 0)
			data.append(buf, n);
		fclose(f);
		return data.find("HeadlessSeeds.txt") != std::string::npos;
	}

	// The exe's leader choice (trace S1, between ICvGame1::Init and Init2): a DLL random
	// number generator seeded with the pregame sync seed; for every active major slot one
	// Get(10000, "Choosing Leader") per leader of its civilization, then setLeaderHead.
	// ASSUMPTION (trace): with several leaders the highest draw wins (every civilization in
	// the VP database has exactly one leader, so the choice cannot be observed).
	void ChooseLeaders(HHNewGameInterfaces& kIf, HHNewGameResult& kResult)
	{
		ICvPreGame1* pg = kIf.pPreGame;
		const unsigned int uiRng = kIf.pContext->CreateRandomNumberGenerator();
		ICvRandom1* pRng = kIf.pContext->GetRandomNumberGenerator(uiRng);
		if(!pRng)
		{
			HHLogError("CreateRandomNumberGenerator failed; leaders not chosen");
			return;
		}
		pRng->Init(pg->syncRandomSeed());
		const int nLeaders = kIf.pContext->GetNumLeaderHeadInfos();
		for(int i = 0; i < kIf.iMaxMajorCivs; ++i)
		{
			const PlayerTypes p = (PlayerTypes)i;
			const SlotStatus eStatus = pg->slotStatus(p);
			if(eStatus != SS_TAKEN && eStatus != SS_COMPUTER)
				continue;
			const CivilizationTypes eCiv = pg->civilization(p);
			if(eCiv == NO_CIVILIZATION || pg->leaderHead(p) != NO_LEADER)
				continue;
			ICvCivilizationInfo1* pCiv = kIf.pContext->GetCivilizationInfo(eCiv);
			if(!pCiv)
				continue;
			int iBest = -1, iBestDraw = -1;
			for(int l = 0; l < nLeaders; ++l)
			{
				if(!pCiv->IsLeaders(l))
					continue;
				const int iDraw = pRng->Get(10000, "Choosing Leader");
				if(iDraw > iBestDraw)
				{
					iBestDraw = iDraw;
					iBest = l;
				}
			}
			pCiv->Destroy();
			if(iBest >= 0)
				HH_STEP(HHFormat("setLeaderHead(%d, %d)", i, iBest), pg->setLeaderHead(p, (LeaderHeadTypes)iBest));
		}
		pRng->Destroy();
		kIf.pContext->DestroyRandomNumberGenerator(uiRng);
	}
}

namespace
{
	int MinorCivId(HHNewGameInterfaces& kIf, const std::string& strType)
	{
		Database::Results r;
		if(kIf.pDb && !strType.empty() && kIf.pDb->Execute(r, "SELECT ID FROM MinorCivilizations WHERE Type = ?") &&
		   r.Bind(1, strType.c_str()) && r.Step())
			return r.GetInt(0);
		return -1;
	}

	std::string MinorCivType(HHNewGameInterfaces& kIf, int iId)
	{
		Database::Results r;
		if(kIf.pDb && iId >= 0 && kIf.pDb->Execute(r, "SELECT Type FROM MinorCivilizations WHERE ID = ?") && r.Bind(1, iId) &&
		   r.Step() && r.GetText(0))
			return r.GetText(0);
		return std::string();
	}

	// The resolved spec's city-state pins (minorCivs: [{index, slot, type, id}], slot MAX_MAJOR_CIVS + index):
	// slot -> minor civ ID, the type looked up in the game database (the spec's id when the database lacks it).
	std::map<int, int> MinorCivPins(const HHJson& kSpec, HHNewGameInterfaces& kIf)
	{
		std::map<int, int> pins;
		const HHJson& kPins = kSpec["minorCivs"];
		for(size_t i = 0; kPins.IsArray() && i < kPins.Size(); ++i)
		{
			const HHJson& e = kPins.At(i);
			const std::string strType = e["type"].AsString();
			const int iIndex = e["index"].AsInt((int)i);
			const int iSlot = e["slot"].AsInt(kIf.iMaxMajorCivs + iIndex);
			int iId = MinorCivId(kIf, strType);
			if(iId < 0)
				iId = e["id"].AsInt(-1);
			else if(e.Has("id") && e["id"].AsInt(-1) != iId)
				HHLogError("spec minorCivs[%u]: %s has ID %d in the game database, not %d", (unsigned)i, strType.c_str(), iId, e["id"].AsInt(-1));
			if(iSlot < kIf.iMaxMajorCivs || iSlot >= MAX_CIV_PLAYERS || iId < 0)
			{
				HHLogError("spec minorCivs[%u]: slot %d, %s (%d) cannot be used", (unsigned)i, iSlot, strType.c_str(), iId);
				continue;
			}
			if(iSlot != kIf.iMaxMajorCivs + iIndex)
				HHLogError("spec minorCivs[%u]: slot %d is not MAX_MAJOR_CIVS (%d) + index %d", (unsigned)i, iSlot, kIf.iMaxMajorCivs, iIndex);
			pins[iSlot] = iId;
		}
		return pins;
	}

	// Random civilizations and city-states for a DLL that leaves them to the exe (the stock DLL; VP chooses them in
	// CvPreGame::onGameStarted, inside closeInactiveSlots): every active major slot without a civilization and every
	// city-state slot (the first numMinorCivs after the majors) without a type. A city-state slot the spec pins
	// (minorCivs) gets that city-state; the others are drawn. The exe's own choice is not known, so a draw (uniform,
	// without repeats, a DLL random number generator seeded with the engine seed) does not reproduce a real game: a
	// spec for a real-game comparison names its civilizations and city-states. A DLL that chooses by itself keeps
	// its choice (the run report compares it with the pins).
	void ChooseRandomCivs(HHNewGameInterfaces& kIf, HHNewGameResult& kResult, const std::map<int, int>& pins)
	{
		ICvPreGame1* pg = kIf.pPreGame;
		std::vector<PlayerTypes> majors, minors;
		std::set<int> civsTaken, minorsTaken;
		for(int i = 0; i < kIf.iMaxMajorCivs; ++i)
		{
			const PlayerTypes p = (PlayerTypes)i;
			if(pg->slotStatus(p) != SS_TAKEN && pg->slotStatus(p) != SS_COMPUTER)
				continue;
			if(pg->civilization(p) == NO_CIVILIZATION)
				majors.push_back(p);
			else
				civsTaken.insert((int)pg->civilization(p));
		}
		const int nMinors = pg->numMinorCivs();
		for(int i = kIf.iMaxMajorCivs; i < MAX_CIV_PLAYERS && i < kIf.iMaxMajorCivs + nMinors; ++i)
		{
			const PlayerTypes p = (PlayerTypes)i;
			if(pg->minorCivType(p) == NO_MINORCIV)
				minors.push_back(p);
			else
				minorsTaken.insert((int)pg->minorCivType(p));
		}
		if(majors.empty() && minors.empty())
			return;
		kResult.hostChoseMinors = !minors.empty();
		// Pinned city-states first: they leave the pool before any draw.
		std::vector<PlayerTypes> drawn;
		for(size_t i = 0; i < minors.size(); ++i)
		{
			const std::map<int, int>::const_iterator it = pins.find((int)minors[i]);
			if(it == pins.end() || minorsTaken.count(it->second))
			{
				if(it != pins.end())
					HHLogError("spec minorCivs: slot %d pins %d, which another slot already has", it->first, it->second);
				drawn.push_back(minors[i]);
				continue;
			}
			HH_STEP(HHFormat("setMinorCivType(%d, %d) /* spec minorCivs */", (int)minors[i], it->second),
			        pg->setMinorCivType(minors[i], (MinorCivTypes)it->second));
			minorsTaken.insert(it->second);
		}
		minors.swap(drawn);
		if(majors.empty() && minors.empty())
			return;
		HHLogError("this DLL leaves %u random civilization(s) and %u city-state(s) to the exe: the host chooses them, "
		           "a real game would choose others", (unsigned)majors.size(), (unsigned)minors.size());
		kResult.hostChoseCivs = true;
		const unsigned int uiRng = kIf.pContext->CreateRandomNumberGenerator();
		ICvRandom1* pRng = kIf.pContext->GetRandomNumberGenerator(uiRng);
		if(!pRng)
			return;
		pRng->Init(kResult.uiExeSeed);
		std::vector<int> civs;
		for(int c = 0; c < kIf.pContext->GetNumCivilizationInfos(); ++c)
		{
			ICvCivilizationInfo1* pCiv = kIf.pContext->GetCivilizationInfo((CivilizationTypes)c);
			if(pCiv && pCiv->IsAIPlayable() && pCiv->IsPlayable() && !civsTaken.count(c))
				civs.push_back(c);
			if(pCiv)
				pCiv->Destroy();
		}
		for(size_t i = 0; i < majors.size() && !civs.empty(); ++i)
		{
			const size_t k = (size_t)pRng->Get((unsigned short)civs.size(), "Host random civilization");
			HH_STEP(HHFormat("setCivilization(%d, %d) /* host choice */", (int)majors[i], civs[k]),
			        pg->setCivilization(majors[i], (CivilizationTypes)civs[k]));
			civs.erase(civs.begin() + k);
		}
		std::vector<int> cityStates;
		for(int m = 0; m < kIf.pContext->GetNumMinorCivInfos(); ++m)
			if(!minorsTaken.count(m) && (!kIf.pDb || !MinorCivType(kIf, m).empty()))  // info arrays may have ID gaps
				cityStates.push_back(m);
		for(size_t i = 0; i < minors.size() && !cityStates.empty(); ++i)
		{
			const size_t k = (size_t)pRng->Get((unsigned short)cityStates.size(), "Host random city-state");
			HH_STEP(HHFormat("setMinorCivType(%d, %d) /* host choice */", (int)minors[i], cityStates[k]),
			        pg->setMinorCivType(minors[i], (MinorCivTypes)cityStates[k]));
			cityStates.erase(cityStates.begin() + k);
		}
		pRng->Destroy();
		kIf.pContext->DestroyRandomNumberGenerator(uiRng);
	}

	// Every city-state slot once the game has started: its city-state, the spec's pin and who chose it.
	void RecordMinorCivs(HHNewGameInterfaces& kIf, HHNewGameResult& kResult, const std::map<int, int>& pins)
	{
		ICvPreGame1* pg = kIf.pPreGame;
		const int nMinors = pg->numMinorCivs();
		for(int i = kIf.iMaxMajorCivs; i < MAX_CIV_PLAYERS && i < kIf.iMaxMajorCivs + nMinors; ++i)
		{
			HHMinorCivSlot s;
			s.slot = i;
			s.id = (int)pg->minorCivType((PlayerTypes)i);
			s.type = MinorCivType(kIf, s.id);
			const std::map<int, int>::const_iterator it = pins.find(i);
			if(it != pins.end())
			{
				s.pinnedId = it->second;
				s.pinnedType = MinorCivType(kIf, it->second);
			}
			kResult.minorCivs.push_back(s);
			if(s.pinnedId >= 0 && s.pinnedId != s.id)
				HHLog("city-state slot %d: %s (%d), the spec pins %s (%d)%s", i, s.type.c_str(), s.id, s.pinnedType.c_str(), s.pinnedId,
				      kResult.hostChoseMinors ? "" : "; the DLL chose it");
		}
	}
}

size_t HHDeliverNetMessages(HHNewGameInterfaces& kIf)
{
	if(!kIf.pEngine || kIf.pEngine->GetPendingNetMessageCount() == 0)
		return 0;
	ICvNetMessageHandler1* pHandler = kIf.pContext->GetNetMessageHandler();
	const size_t n = kIf.pEngine->DeliverNetMessages(pHandler);
	if(pHandler)
		pHandler->Destroy();
	return n;
}

bool HHStartNewGame(const HHJson& kSpec, HHNewGameInterfaces& kIf, HHNewGameResult& kResult)
{
	ICvPreGame1* pg = kIf.pPreGame;
	double t0 = Now();

	// 1. Premade map (trace S3, hh-m0-s3s4-earth): Preload(path, loadWBScenario), map info,
	//    setMapNoPlayers, the map's world size (no slot reset), all before closeInactiveSlots.
	//    The exe passes the game-relative name; the host passes the full path (its working
	//    directory is the run folder).
	if(pg->isWBMapScript())
	{
		std::string path = Slashes(kSpec["map"]["file"].AsString());
		if(!IsAbsolutePath(path))
			path = HHGetOptions().gameDir + "\\" + path;
		bool bLoaded = false;
		HH_STEP(HHFormat("WorldBuilderMapLoader.Preload(\"%s\", false)", path.c_str()), bLoaded = kIf.pWBLoader->Preload(HHWiden(path).c_str(), pg->loadWBScenario()));
		if(!bLoaded)
		{
			kResult.error = "Preload failed for " + path;
			return false;
		}
		const CvWorldBuilderMapLoaderMapInfo& info = kIf.pWBLoader->GetCurrentMapInfo();
		HHLog("premade map %s: %ux%u, %u players, %u city-states, %u/%u start positions, wrap %d, random resources %d", path.c_str(),
		      info.uiWidth, info.uiHeight, info.uiPlayers, info.uiCityStates, info.uiMajorCivStartingPositions,
		      info.uiMinorCivStartingPositions, info.bWorldWrap ? 1 : 0, info.bRandomResources ? 1 : 0);
		// ASSUMPTION (trace): the flag is "the map file has no players" (Earth: true in S3).
		const bool bNoPlayers = info.uiPlayers == 0;
		HH_STEP(HHFormat("setMapNoPlayers(%s)", bNoPlayers ? "true" : "false"), pg->setMapNoPlayers(bNoPlayers));
		ICvWorldBuilderMapLoader2* pWB2 = kIf.pWBLoader->QueryInterface<ICvWorldBuilderMapLoader2>();
		if(pWB2)
		{
			const WorldSizeTypes eSize = pWB2->GetWorldSizeType();
			HH_STEP(HHFormat("setWorldSize(%d, false)", (int)eSize), pg->setWorldSize(eSize, false));
		}
	}

	// 2. Seeds. The real driver writes <cache>\HeadlessSeeds.txt before the game starts; the DLL
	//    applies it at setGameStarted(true) (just before onGameStarted, HHSEEDS point=gameStarted,
	//    255e26ba2) and consumes it at the start of CvGame::init (point=gameInit). A DLL without
	//    seed pinning gets the seeds in pregame directly, just before closeInactiveSlots.
	const unsigned int uiMapSeed = SeedValue(kSpec["seeds"]["map"]);
	const unsigned int uiSyncSeed = SeedValue(kSpec["seeds"]["sync"]);
	if(DllHasSeedPinning(HHGetOptions().dllPath))
	{
		const std::string seedFile = HHGetPaths().cache + "HeadlessSeeds.txt";
		// Same keys as vpbench's driver (digest=1: per-turn digest logging from turn 0).
		std::string seeds = HHFormat("map=%u\nsync=%u\ndigest=1\n", uiMapSeed, uiSyncSeed);
		// --dump-turns FROM:TO: the DLL dumps the state at doTurnBegin of those turns (Logs\HeadlessDumps).
		if(HHGetOptions().dumpFrom >= 0)
			seeds += HHFormat("dumpfrom=%d\ndumpto=%d\n", HHGetOptions().dumpFrom, HHGetOptions().dumpTo);
		HHWriteTextFile(seedFile, seeds);
		kResult.steps.push_back(HHFormat("write %s: map=%u sync=%u", seedFile.c_str(), uiMapSeed, uiSyncSeed));
		kResult.seedMode = "HeadlessSeeds.txt";
	}
	else
	{
		HH_STEP(HHFormat("setMapRandomSeed(%u)", uiMapSeed), pg->setMapRandomSeed(uiMapSeed));
		HH_STEP(HHFormat("setSyncRandomSeed(%u)", uiSyncSeed), pg->setSyncRandomSeed(uiSyncSeed));
		kResult.seedMode = "pregame";
	}

	// 3. closeInactiveSlots marks the game started, which runs VP's CvPreGame::onGameStarted:
	//    random civilizations for slots without one and the city-state selection, drawn with
	//    the pregame map seed (CvGame::urandLimitExclusive), i.e. the spec's map seed. Then open slots are closed and player
	//    info is sent (no-op single player).
	//    A DLL that leaves them to the exe gets the host's choice, with the spec's city-state pins (minorCivs).
	HH_STEP("closeInactiveSlots()", pg->closeInactiveSlots());
	const std::map<int, int> minorPins = MinorCivPins(kSpec, kIf);
	ChooseRandomCivs(kIf, kResult, minorPins);
	kResult.timings.push_back(std::make_pair(std::string("launch"), Now() - t0));

	// 4. ICvGame1::Init(handicap of the active player): reset, RNGs from the pregame seeds,
	//    game id, start turn and max turns.
	t0 = Now();
	const HandicapTypes eHandicap = pg->handicap(pg->activePlayer());
	HH_STEP(HHFormat("Game.Init(%d)", (int)eHandicap), kIf.pGame->Init(eHandicap));
	if(pg->mapRandomSeed() != uiMapSeed || pg->syncRandomSeed() != uiSyncSeed)
		HHLogError("seeds after Game.Init: map %u sync %u, expected %u/%u", pg->mapRandomSeed(), pg->syncRandomSeed(), uiMapSeed, uiSyncSeed);
	if(kIf.pDbUtil)
		HH_STEP("CacheGameDatabaseData()", kIf.pDbUtil->CacheGameDatabaseData());
	ChooseLeaders(kIf, kResult);
	for(int i = 0; i < kIf.iMaxMajorCivs; ++i)
	{
		const PlayerTypes p = (PlayerTypes)i;
		if(pg->slotStatus(p) == SS_TAKEN || pg->slotStatus(p) == SS_COMPUTER)
			HHLog("  slot %2d: status %d civ %d leader %d team %d handicap %d", i, (int)pg->slotStatus(p), (int)pg->civilization(p),
			      (int)pg->leaderHead(p), (int)pg->teamType(p), (int)pg->handicap(p));
	}
	kResult.timings.push_back(std::make_pair(std::string("game_init"), Now() - t0));

	// 5. ICvGame1::Init2: players (colours, AI handicaps, city-state slots), map
	//    (CvMapGenerator: the map script's GetMapInitData and GenerateMap, start positions;
	//    or the premade map), diplomacy, initial units, score, final initialisation.
	t0 = Now();
	bool bInit2 = false;
	HH_STEP("Game.Init2()", bInit2 = kIf.pGame->Init2());
	kResult.timings.push_back(std::make_pair(std::string("game_init2_mapgen"), Now() - t0));
	if(!bInit2)
	{
		kResult.error = "ICvGame1::Init2 returned false";
		return false;
	}
	RecordMinorCivs(kIf, kResult, minorPins);

	// 6. After Init2: HHGameStartedSteps.
	t0 = Now();
	HHGameStartedSteps(kIf, kResult, false);
	kResult.timings.push_back(std::make_pair(std::string("game_started"), Now() - t0));

	kResult.iWidth = kIf.pMap->GetGridWidth();
	kResult.iHeight = kIf.pMap->GetGridHeight();
	HHLog("game started: map %dx%d (%d plots), turn %d, final initialised %d", kResult.iWidth, kResult.iHeight, kIf.pMap->NumPlots(),
	      kIf.pGame->GetGameTurn(), kIf.pGame->IsFinalInitialized() ? 1 : 0);
	kResult.bStarted = true;
	return true;
}

void HHGameStartedSteps(HHNewGameInterfaces& kIf, HHNewGameResult& kResult, bool bLoaded)
{
	ICvPreGame1* pg = kIf.pPreGame;
	// After Init2 (trace S1): LaterInit for every player slot, InitTacticalAnalysisMap
	// (a no-op in VP), the in-game UI's SetGameOption calls, then DoGameStarted (and
	// InitExeStuff: BIN_UNKNOWN for this exe). The exe's SetInitialTime/SetStartTime (wall
	// clock) are not called.
	// ASSUMPTION (no trace of a load yet): after a load the engine makes the same calls, without closing the slots of
	// city-states (the save's pregame has them) and with the unit cycle instead of the settler selection.
	// CvPlayer::gameStartInit says it runs "also after reload", and DoGameStarted fills caches the save does not hold.
	for(int i = 0; i < MAX_PLAYERS; ++i)
	{
		ICvPlayer1* pPlayer = kIf.pGame->GetPlayer((PlayerTypes)i);
		if(pPlayer)
		{
			pPlayer->LaterInit();
			pPlayer->Destroy();
		}
	}
	kResult.steps.push_back(HHFormat("for i = 0..%d: Game.GetPlayer(i).LaterInit()", MAX_PLAYERS - 1));
	// ASSUMPTION (M5 references vp-ring, vp-north-vs-south, players-cs41): the exe closes the slots of
	// city-states that did not survive map generation (AssignStartingPlots discards them when no site is
	// left); the real pregame has SS_CLOSED there. CvGame looks for SS_CLOSED minor slots when it creates
	// a city-state later in the game.
	for(int i = kIf.iMaxMajorCivs; i < MAX_CIV_PLAYERS && !bLoaded; ++i)
	{
		const PlayerTypes p = (PlayerTypes)i;
		if(pg->slotStatus(p) != SS_COMPUTER)
			continue;
		ICvPlayer1* pPlayer = kIf.pGame->GetPlayer(p);
		const bool bAlive = pPlayer && pPlayer->IsAlive();
		if(pPlayer)
			pPlayer->Destroy();
		if(!bAlive)
			HH_STEP(HHFormat("setSlotStatus(%d, SS_CLOSED) /* city-state not alive after map generation */", i), pg->setSlotStatus(p, SS_CLOSED));
	}
	const int iPlots = kIf.pMap->NumPlots();
	HH_STEP(HHFormat("Game.InitTacticalAnalysisMap(%d)", iPlots), kIf.pGame->InitTacticalAnalysisMap(iPlots));
	// ASSUMPTION (trace): the in-game UI's start-up sets these three options to 0 (S1 shows
	// the calls between InitTacticalAnalysisMap and DoGameStarted).
	HH_STEP("SetGameOption(\"GAMEOPTION_DYNAMIC_TURNS\", 0)", pg->SetGameOption("GAMEOPTION_DYNAMIC_TURNS", 0));
	HH_STEP("SetGameOption(\"GAMEOPTION_SIMULTANEOUS_TURNS\", 0)", pg->SetGameOption("GAMEOPTION_SIMULTANEOUS_TURNS", 0));
	HH_STEP("SetGameOption(\"GAMEOPTION_PITBOSS\", 0)", pg->SetGameOption("GAMEOPTION_PITBOSS", 0));
	// Trace S1/S3b (contract 5.1, 5.4 step 7): the exe switches the DLL to graphical mode and
	// sets up every player's graphics before DoGameStarted; the visual OUT calls are no-ops here.
	HH_STEP("GameContext.SetGraphicsInitialized(true)", kIf.pContext->SetGraphicsInitialized(true));
	for(int i = 0; i < MAX_PLAYERS; ++i)
	{
		ICvPlayer1* pPlayer = kIf.pGame->GetPlayer((PlayerTypes)i);
		if(pPlayer)
		{
			pPlayer->SetupGraphical();
			pPlayer->Destroy();
		}
	}
	kResult.steps.push_back(HHFormat("for i = 0..%d: Game.GetPlayer(i).SetupGraphical()", MAX_PLAYERS - 1));
	HH_STEP("Game.DoGameStarted()", kIf.pGame->DoGameStarted());
	// Trace S1, before the first ICvGame1::Update: NetMessageStaticsReset, then
	// SendPlayerOptions(true) (the DLL reads each option from the engine and sends it), and
	// the engine clears the network-sync deltas and delivers the sent values back as
	// NetMessageHandler::ResponsePlayerOption for the active player (same frame).
	if(ICvGame2* pGame2 = kIf.pGame->QueryInterface<ICvGame2>())
		HH_STEP("Game.NetMessageStaticsReset()", pGame2->NetMessageStaticsReset());
	HH_STEP("Game.SendPlayerOptions(true)", kIf.pGame->SendPlayerOptions(true));
	if(ICvNetworkSyncronization1* pSync = kIf.pContext->GetNetworkSyncronizer())
	{
		pSync->ClearCityDeltas();
		pSync->ClearRandomDeltas();
		pSync->ClearPlayerDeltas();
		pSync->ClearPlotDeltas();
		pSync->ClearUnitDeltas();
		pSync->Destroy();
		kResult.steps.push_back("NetworkSyncronizer.Clear{City,Random,Player,Plot,Unit}Deltas()");
	}
	if(kIf.pEngine)
	{
		const size_t nOptions = HHDeliverNetMessages(kIf);
		kResult.steps.push_back(HHFormat("NetMessageHandler.ResponsePlayerOption(%d, option, value) x%u", (int)pg->activePlayer(), (unsigned)nOptions));
	}
	// The in-game UI's first frame (trace S1/S3b): SelectSettler (selects the active player's
	// settler: increments its serialized m_iEverSelectedCount) and the active player's
	// RebroadcastNotifications.
	// ASSUMPTION (M5 era-medieval/industrial/future, no trace yet): for a later-era start (game turn > 0)
	// the real game has no settler selected at the start dump but the unit nearest to plot (0,0), which is
	// what UpdateSelectionList picks off turn 0 (CvGame::cycleUnits -> CvMap::findUnit): unit 1004 in
	// era-medieval. So the first frame is taken to call UpdateSelectionList instead of SelectSettler there.
	if(kIf.pGame->GetGameTurn() == 0 && !bLoaded)
		HH_STEP("Game.SelectSettler()", kIf.pGame->SelectSettler());
	else
		HH_STEP(HHFormat("Game.UpdateSelectionList() /* %s turn %d: no SelectSettler */", bLoaded ? "loaded at" : "start", kIf.pGame->GetGameTurn()),
		        kIf.pGame->UpdateSelectionList());
	if(ICvPlayer1* pActive = kIf.pGame->GetPlayer(pg->activePlayer()))
	{
		HH_STEP(HHFormat("Game.GetPlayer(%d).RebroadcastNotifications()", (int)pg->activePlayer()), pActive->RebroadcastNotifications());
		pActive->Destroy();
	}
}

//------------------------------------------------------------------------------
// Task 5: T000 outputs
//------------------------------------------------------------------------------
namespace
{
	// Map summary: everything map generation decided, by type name. M calls a method only some DLLs have (VP's
	// GetLandmass, IsNaturalWonder).
	const char* kMapLua =
		"local function T(tbl, id) if id == nil or id < 0 then return nil end local r = tbl[id] return r and r.Type or id end\n"
		"local function M(o, name) local f = o[name] if f then return f(o) end end\n"
		"local w, h = Map.GetGridSize()\n"
		"local out = {format = 'civ5headless-t000-map/1', width = w, height = h, wrapX = Map.IsWrapX(), wrapY = Map.IsWrapY(),\n"
		"  numPlots = Map.GetNumPlots(), turn = Game.GetGameTurn(), players = {}, cityStates = {}, plots = {}}\n"
		"for i = 0, 63 do\n"
		"  local p = Players[i]\n"
		"  if p and p:IsEverAlive() then\n"
		"    local sp = p:GetStartingPlot()\n"
		"    local e = {slot = i, civilization = T(GameInfo.Civilizations, p:GetCivilizationType()), leader = T(GameInfo.Leaders, p:GetLeaderType()),\n"
		"      team = p:GetTeam(), minor = p:IsMinorCiv(), barbarian = p:IsBarbarian(), human = p:IsHuman(),\n"
		"      handicap = T(GameInfo.HandicapInfos, p:GetHandicapType()), startX = sp and sp:GetX() or nil, startY = sp and sp:GetY() or nil,\n"
		"      units = p:GetNumUnits(), cities = p:GetNumCities()}\n"
		"    if p:IsMinorCiv() then e.minorCiv = T(GameInfo.MinorCivilizations, p:GetMinorCivType()); out.cityStates[#out.cityStates + 1] = e.minorCiv end\n"
		"    local units = {}\n"
		"    for u in p:Units() do units[#units + 1] = {T(GameInfo.Units, u:GetUnitType()), u:GetX(), u:GetY()} end\n"
		"    e.unitList = units\n"
		"    out.players[#out.players + 1] = e\n"
		"  end\n"
		"end\n"
		"for i = 0, Map.GetNumPlots() - 1 do\n"
		"  local pl = Map.GetPlotByIndex(i)\n"
		"  local res = pl:GetResourceType(-1)\n"
		"  out.plots[#out.plots + 1] = {i = i, x = pl:GetX(), y = pl:GetY(), plotType = pl:GetPlotType(), terrain = T(GameInfo.Terrains, pl:GetTerrainType()),\n"
		"    feature = T(GameInfo.Features, pl:GetFeatureType()), resource = T(GameInfo.Resources, res), resourceAmount = res >= 0 and pl:GetNumResource() or 0,\n"
		"    riverW = pl:IsWOfRiver(), riverNW = pl:IsNWOfRiver(), riverNE = pl:IsNEOfRiver(),\n"
		"    flowE = pl:GetRiverEFlowDirection(), flowSE = pl:GetRiverSEFlowDirection(), flowSW = pl:GetRiverSWFlowDirection(),\n"
		"    improvement = T(GameInfo.Improvements, pl:GetImprovementType()), owner = pl:GetOwner(), area = pl:GetArea(),\n"
		"    landmass = M(pl, 'GetLandmass'), continentArt = M(pl, 'GetContinentArtType'), naturalWonder = M(pl, 'IsNaturalWonder') or nil}\n"
		"end\n"
		"return out\n";

	// Verbatim copy of vpbench.py CHECKPOINT (work/vpbench/vpbench.py); keep in sync.
	const char* kCheckpointLua =
		"local active=Game.GetActivePlayer()\n"
		"local out={turn=Game.GetGameTurn(),activePlayer=active,autoplay=Game.GetAIAutoPlay(),observer=Players[active]:IsObserver(),human=Players[active]:IsHuman(),pausePlayer=Game.GetPausePlayer(),replayCount=Game.GetNumReplayMessages(),winner=Game.GetWinner(),players={},cities={},wars={},livingMajors={}}\n"
		"for owner=0,63 do\n"
		" local p=Players[owner]\n"
		" if p and p:IsAlive() and not p:IsObserver() then\n"
		"  local civ=GameInfo.Civilizations[p:GetCivilizationType()]\n"
		"  out.players[#out.players+1]={owner=owner,team=p:GetTeam(),civilization=civ and civ.Type or '',minor=p:IsMinorCiv(),barbarian=p:IsBarbarian(),unitCount=p:GetNumUnits(),cityCount=p:GetNumCities(),military=p:GetNumMilitaryUnits(),score=(not p:IsMinorCiv() and not p:IsBarbarian()) and p:GetScore() or 0,techs=Teams[p:GetTeam()]:GetTeamTechs():GetNumTechsKnown()}\n"
		"  if not p:IsMinorCiv() and not p:IsBarbarian() then out.livingMajors[#out.livingMajors+1]=owner end\n"
		"  for city in p:Cities() do\n"
		"   out.cities[#out.cities+1]={plot=city:Plot():GetPlotIndex(),owner=owner,name=city:GetName(),originalOwner=city:GetOriginalOwner(),foundedTurn=city:GetGameTurnFounded(),acquiredTurn=city:GetGameTurnAcquired(),hp=city:GetMaxHitPoints()-city:GetDamage(),maxHP=city:GetMaxHitPoints(),population=city:GetPopulation()}\n"
		"  end\n"
		"  if not p:IsBarbarian() then\n"
		"   for other=owner+1,63 do local q=Players[other];if q and q:IsAlive() and not q:IsObserver() and not q:IsBarbarian() and Teams[p:GetTeam()]:IsAtWar(q:GetTeam()) then out.wars[#out.wars+1]={owner,other} end end\n"
		"  end\n"
		" end\n"
		"end\n"
		"local camp=GameInfoTypes.IMPROVEMENT_BARBARIAN_CAMP;out.barbCamps=0\n"
		"for i=0,Map.GetNumPlots()-1 do if Map.GetPlotByIndex(i):GetImprovementType()==camp then out.barbCamps=out.barbCamps+1 end end\n"
		"return out\n";

	// Game.GetHeadlessDigest (hh-digest, docs/headless-dll-additions.md): the DLL's state digest
	// at T000. Absent from DLLs built without the headless additions.
	const char* kDigestLua =
		"if Game.GetHeadlessDigest == nil then return nil end\n"
		"return Game.GetHeadlessDigest()\n";

	// Runs a chunk that returns one value in a fresh thread; leaves the value on the thread
	// stack (index -1) and returns the thread, or NULL on error.
	lua_State* RunReturning(HHScriptSystem& kScripts, const char* szName, const char* szChunk)
	{
		lua_State* L = kScripts.CreateLuaThread(szName);
		if(!L)
			return NULL;
		if(luaL_loadbuffer(L, szChunk, strlen(szChunk), szName) != 0)
		{
			HHLogError("%s: %s", szName, lua_tostring(L, -1));
			kScripts.FreeLuaThread(L);
			return NULL;
		}
		if(!kScripts.CallFunction(L, 0, 1))
		{
			kScripts.FreeLuaThread(L);
			return NULL;
		}
		return L;
	}

	std::string UtcNow()
	{
		SYSTEMTIME st;
		GetSystemTime(&st);
		return HHFormat("%04d-%02d-%02dT%02d:%02d:%02d.%03dZ", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);
	}

	// Writes the map table: top level pretty-printed, one plot per line.
	std::string MapJson(lua_State* L, int idx)
	{
		std::vector<std::string> keys;
		lua_pushnil(L);
		while(lua_next(L, idx) != 0)
		{
			if(lua_type(L, -2) == LUA_TSTRING)
				keys.push_back(lua_tostring(L, -2));
			lua_pop(L, 1);
		}
		std::sort(keys.begin(), keys.end());
		std::string out = "{";
		for(size_t i = 0; i < keys.size(); ++i)
		{
			out += i ? ",\n " : "\n ";
			out += "\"" + HHJsonEscape(keys[i]) + "\": ";
			lua_getfield(L, idx, keys[i].c_str());
			if((keys[i] == "plots" || keys[i] == "players") && lua_istable(L, -1))
			{
				const int t = lua_gettop(L);
				const int n = (int)lua_objlen(L, t);
				out += "[";
				for(int k = 1; k <= n; ++k)
				{
					lua_rawgeti(L, t, k);
					out += k > 1 ? ",\n  " : "\n  ";
					out += HHLuaToJson(L, -1, 0);
					lua_pop(L, 1);
				}
				out += n ? "\n ]" : "]";
			}
			else
				out += HHLuaToJson(L, -1, 0);
			lua_pop(L, 1);
		}
		out += "\n}\n";
		return out;
	}
}

//------------------------------------------------------------------------------
// The in-game UI: emulation profiles, the load screen (the state the reference runs dump as dump-t000), UI add-ins
//------------------------------------------------------------------------------
void HHStartUI(HHNewGameInterfaces& kIf, HHNewGameResult& kResult)
{
	HHRegisterUIBindings(*kIf.pScripts, kIf.pGame, kIf.pPreGame, kIf.pUI);
	HHProfiles& kProfiles = HHGetProfiles();
	kProfiles.Select(kIf.pDb);
	kProfiles.Start(*kIf.pScripts);
	for(size_t i = 0; i < kProfiles.GetProfiles().size(); ++i)
		if(kProfiles.GetProfiles()[i].selected)
			kResult.steps.push_back("profile " + kProfiles.GetProfiles()[i].name + " (" + kProfiles.GetProfiles()[i].hook + ")");
}

bool HHLoadScreen(HHNewGameInterfaces& kIf, HHNewGameResult& kResult)
{
	ICvPreGame1* pg = kIf.pPreGame;
	// LoadScreen.lua: Game.SetPausePlayer(Game.GetActivePlayer()) once the game is initialised
	// (contract 1.10); Game.SetPausePlayer(-1) only on "continue", after the reference dump.
	HH_STEP(HHFormat("Game.SetPausePlayer(%d)", (int)pg->activePlayer()), kIf.pGame->SetPausePlayer(pg->activePlayer()));
	// UI Lua that runs while the load screen is up and leaves serialized state behind (contract sections 1 and 2):
	// load-screen profiles (base game: Demographics.lua BuildTables).
	const int iErrors = kIf.pScripts->GetErrorCount();
	HHGetProfiles().RunHook(*kIf.pScripts, "load-screen", &kResult.steps);
	return kIf.pScripts->GetErrorCount() == iErrors;
}

void HHLoadUIAddinsStep(HHNewGameInterfaces& kIf, HHNewGameResult& kResult)
{
	if(HHGetOptions().noAddins)
		kResult.steps.push_back("UI add-ins not loaded (--no-addins)");
	else
	{
		kResult.uiAddins = HHLoadUIAddins(*kIf.pScripts, HHGetOptions().skipAddins);
		for(size_t i = 0; i < kResult.uiAddins.size(); ++i)
		{
			const HHAddinContext& c = kResult.uiAddins[i];
			if(c.status == "run")
				kResult.addins.push_back(c.path);
			kResult.steps.push_back("InGameUIAddin " + c.context + ": " + c.status + (c.path.empty() ? std::string() : " " + c.path));
		}
	}
	// The engine's event once the in-game UI has loaded (the load screen then offers "continue").
	HHFireUIEvent(*kIf.pScripts, "SequenceGameInitComplete", std::string());
}

void HHRunLuaProbes(HHNewGameInterfaces& kIf, HHNewGameResult& kResult)
{
	const std::vector<std::string>& probes = HHGetOptions().luaProbes;
	if(probes.empty())
		return;
	// One InGame context for every probe file, in order, as FireTuner injects them into the in-game Lua state; kept for
	// the game (the probes' listeners close over it).
	lua_State* L = HHLuaCreateUIContext(*kIf.pScripts, "InGame", false);
	for(size_t i = 0; i < probes.size() && L; ++i)
	{
		const bool bOk = kIf.pScripts->RunFile(L, probes[i]);
		kResult.steps.push_back("--lua-probe " + probes[i] + (bOk ? " ok" : " failed"));
		if(!bOk)
			kResult.probeErrors.push_back(probes[i]);
	}
}

bool HHWriteT000(const HHJson& kSpec, HHNewGameInterfaces& kIf, HHNewGameResult& kResult)
{
	UNUSED_VARIABLE(kSpec);
	const double t0 = Now();
	const std::string run = HHGetPaths().run;
	bool bOk = true;

	// ------------------------------------------------------------------------------------
	// HEADLESS DIGEST: Game.GetHeadlessDigest() at T000 (DLL additions D2), first: the
	// map summary and the checkpoint below call lazy getters that can change state. Written to
	// t000-digest.json when the DLL provides it; the total goes into newgame-report.json.
	// ------------------------------------------------------------------------------------
	if(lua_State* L = RunReturning(*kIf.pScripts, "Headless digest", kDigestLua))
	{
		if(lua_istable(L, -1))
		{
			bOk &= HHWriteTextFile(run + "t000-digest.json", HHLuaToJson(L, -1, 1) + "\n");
			lua_getfield(L, -1, "total");
			if(lua_isstring(L, -1))
				kResult.digestTotal = lua_tostring(L, -1);
			lua_pop(L, 1);
		}
		else
			HHLog("Game.GetHeadlessDigest is not available in this DLL; no t000-digest.json");
		kIf.pScripts->FreeLuaThread(L);
	}
	// Raw section bytes (<run>\dump-t000\<section>.bin + digest.txt), the same layout as the
	// real-game probe dumps, for byte-level comparison (tools/headless/hhdigest.py). --no-dumps: none (the dump
	// computes the digest sections and only reads the game).
	if(!HHGetOptions().noDumps)
	{
		std::string folder = run + "dump-t000";
		std::string chunk = "if Game.DumpHeadlessState == nil then return nil end\nreturn Game.DumpHeadlessState([==[" + folder + "]==])\n";
		if(lua_State* L = RunReturning(*kIf.pScripts, "Headless dump", chunk.c_str()))
			kIf.pScripts->FreeLuaThread(L);
	}

	// t000-map.json: deterministic (no times); identical across runs of the same spec.
	if(lua_State* L = RunReturning(*kIf.pScripts, "Headless T000 map", kMapLua))
	{
		if(lua_istable(L, -1))
			bOk &= HHWriteTextFile(run + "t000-map.json", MapJson(L, lua_gettop(L)));
		else
			bOk = false;
		kIf.pScripts->FreeLuaThread(L);
	}
	else
		bOk = false;

	// checkpoints/turn-0000.json: vpbench's CHECKPOINT result, wrapped as vpbench writes it.
	HHEnsureDirectory(run + "checkpoints");
	if(lua_State* L = RunReturning(*kIf.pScripts, "Headless checkpoint", kCheckpointLua))
	{
		const std::string json = "{\n \"utc\": \"" + UtcNow() + "\",\n \"data\": " + HHLuaToJson(L, -1, 0) + "\n}\n";
		// A loaded game starts at the save's turn.
		bOk &= HHWriteTextFile(run + HHFormat("checkpoints\\turn-%04d.json", HHIsLoadedGame() ? kIf.pGame->GetGameTurn() : 0), json);
		kIf.pScripts->FreeLuaThread(L);
	}
	else
		bOk = false;

	kResult.timings.push_back(std::make_pair(std::string("t000_outputs"), Now() - t0));
	kResult.bOutputsOk = bOk;
	return bOk;
}

void HHEndNewGame(HHNewGameInterfaces& kIf, HHNewGameResult& kResult)
{
	// ASSUMPTION (trace): leaving a game, the exe clears pregame's started flag before
	// ICvGame1::Uninit (CvDllGame::Uninit traps while it is set).
	HH_STEP("setGameStarted(false)", kIf.pPreGame->setGameStarted(false));
	HH_STEP("Game.Uninit()", kIf.pGame->Uninit());
}

//------------------------------------------------------------------------------
// M3: the autoplay turn loop
//------------------------------------------------------------------------------
namespace
{
	int LuaInt(HHScriptSystem& kScripts, const char* szChunk, int iDefault)
	{
		int v = iDefault;
		if(lua_State* L = RunReturning(kScripts, "Headless query", szChunk))
		{
			if(lua_isnumber(L, -1))
				v = (int)lua_tointeger(L, -1);
			kScripts.FreeLuaThread(L);
		}
		return v;
	}

	// The engine's frame for a human active player (trace hh-m0-s1c-continents after autoplay ends):
	// on the active player's turn start EndTurnTimerReset, UpdateSelectionList (auto unit cycle: selects
	// a unit, CvUnit::m_iEverSelectedCount), RebroadcastNotifications and SetBusyUnitUpdatesRemaining(600);
	// then every frame UpdateTestEndTurn (CvPlayer::m_eEndTurnBlockingType).
	void HumanTurnFrame(HHNewGameInterfaces& kIf)
	{
		// A popup that is up (WhosWinning) keeps the engine from cycling units and testing the end of
		// turn (hh-m2-aa100-a T100: m_eEndTurnBlockingType -1, no unit ever selected).
		if(kIf.pUI->IsPopupShown())
			return;
		const bool bTurnStart = kIf.pUI->TakeActivePlayerTurnStart();
		const PlayerTypes eActive = kIf.pPreGame->activePlayer();
		ICvPlayer1* pPlayer = kIf.pGame->GetPlayer(eActive);
		if(!pPlayer)
			return;
		if(pPlayer->IsHuman() && pPlayer->IsAlive() && pPlayer->IsTurnActive())
		{
			if(bTurnStart)
			{
				kIf.pGame->EndTurnTimerReset();
				kIf.pGame->UpdateSelectionList();
				pPlayer->RebroadcastNotifications();
				pPlayer->SetBusyUnitUpdatesRemaining(600);
			}
			kIf.pGame->UpdateTestEndTurn();
		}
		pPlayer->Destroy();
	}

	// SerialEventGameMessagePopup on the UI frame after AddPopup: the popup profiles emulate the popups whose Lua has
	// game state effects or stays up (setup/HHProfiles.h); every popup is logged.
	void ShowQueuedPopups(HHNewGameInterfaces& kIf, HHNewGameResult& kResult)
	{
		std::vector<CvPopupInfo> popups = kIf.pUI->TakeQueuedPopups();
		for(size_t i = 0; i < popups.size(); ++i)
		{
			std::string what = HHFormat("popup %d (data %d,%d,%d) at turn %d", (int)popups[i].eButtonPopupType, popups[i].iData1,
			                            popups[i].iData2, popups[i].iData3, kIf.pGame->GetGameTurn());
			bool bStaysUp = false;
			HHGetProfiles().RunPopup(*kIf.pScripts, popups[i], bStaysUp, what);
			if(bStaysUp)
				kIf.pUI->SetPopupUp(true);
			HHLog("%s", what.c_str());
			kResult.steps.push_back(what);
		}
	}

	// The notification panel's handlers run on the UI frame after a NotificationAdded (notification-added profiles).
	void NotificationAdded(HHNewGameInterfaces& kIf)
	{
		if(!kIf.pUI->TakeNotificationAdded())
			return;
		HHGetProfiles().RunHook(*kIf.pScripts, "notification-added", NULL);
	}

	// M6 tooling: runs a Lua file in a fresh thread and returns its result as JSON ("null" when it
	// returns nothing; empty when the file cannot be read or raises).
	std::string RunFileToJson(HHScriptSystem& kScripts, const std::string& file, const char* szName)
	{
		std::string chunk;
		if(!HHReadTextFile(file, chunk))
		{
			HHLogError("%s: cannot read %s", szName, file.c_str());
			return std::string();
		}
		std::string json;
		if(lua_State* L = RunReturning(kScripts, szName, chunk.c_str()))
		{
			json = lua_isnoneornil(L, -1) ? std::string("null") : HHLuaToJson(L, -1, 0);
			kScripts.FreeLuaThread(L);
		}
		return json;
	}

	// M6 tooling: appends one line to a run file with a Win32 handle (the DLL's logs can exhaust the CRT's streams).
	void AppendLine(const std::string& path, const std::string& line)
	{
		HANDLE h = CreateFileW(HHWiden(path).c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
		                       FILE_ATTRIBUTE_NORMAL, NULL);
		if(h == INVALID_HANDLE_VALUE)
			return;
		const std::string text = line + "\n";
		DWORD written = 0;
		WriteFile(h, text.data(), (DWORD)text.size(), &written, NULL);
		CloseHandle(h);
	}

	// M6 tooling: checkpoints\turn-NNNN[-final].json from --checkpoint-lua, wrapped as vpbench writes them.
	void WriteCheckpoint(HHScriptSystem& kScripts, int iTurn, const char* szSuffix, double dSeconds)
	{
		const std::string json = RunFileToJson(kScripts, HHGetOptions().checkpointLua, "Headless checkpoint-lua");
		if(json.empty())
			return;
		HHWriteTextFile(HHGetPaths().run + HHFormat("checkpoints\\turn-%04d%s.json", iTurn, szSuffix),
		                "{\n \"utc\": \"" + UtcNow() + "\",\n \"seconds\": " + HHFormat("%.3f", dSeconds) + ",\n \"data\": " + json + "\n}\n");
	}

	std::string DumpState(HHScriptSystem& kScripts, const std::string& folder)
	{
		std::string chunk = "if Game.DumpHeadlessState == nil then return nil end\nreturn Game.DumpHeadlessState([==[" + folder + "]==])\n";
		std::string line;
		if(lua_State* L = RunReturning(kScripts, "Headless dump", chunk.c_str()))
		{
			if(lua_isstring(L, -1))
				line = lua_tostring(L, -1);
			kScripts.FreeLuaThread(L);
		}
		return line;
	}
}

bool HHRunAutoplay(HHNewGameInterfaces& kIf, HHNewGameResult& kResult, int nTurns, int nIdleUpdates)
{
	const double t0 = Now();
	HHScriptSystem& kScripts = *kIf.pScripts;
	const HHOptions& o = HHGetOptions();
	// M6 tooling (--lua-before-arm): vpbench sets the diagnostics level (and the A/B mask) here.
	if(!o.luaBeforeArm.empty())
	{
		const std::string json = RunFileToJson(kScripts, o.luaBeforeArm, "--lua-before-arm");
		HHWriteTextFile(HHGetPaths().run + "lua-before-arm.json", (json.empty() ? std::string("null") : json) + "\n");
		kResult.steps.push_back("--lua-before-arm " + o.luaBeforeArm + (json.empty() ? " failed" : " ok"));
	}
	// ARM (work/headless/game_probe.py, InGame context, while the load screen is up): quick combat and
	// quick movement are game options (serialized state); SetAIAutoPlay moves the active player to an
	// observer slot and re-sends the player options for it.
	const std::string arm = HHFormat(
		"Game.SetOption('GAMEOPTION_QUICK_COMBAT',true);Game.SetOption('GAMEOPTION_QUICK_MOVEMENT',true);"
		"Game.SetAIAutoPlay(%d,0);return Game.GetAIAutoPlay()", nTurns);
	int iArmed = 0;
	if(lua_State* L = RunReturning(kScripts, "Headless arm-autoplay", arm.c_str()))
	{
		iArmed = (int)lua_tointeger(L, -1);
		kScripts.FreeLuaThread(L);
	}
	kResult.steps.push_back(HHFormat("ARM: QUICK_COMBAT, QUICK_MOVEMENT, Game.SetAIAutoPlay(%d, 0) -> %d; active player %d", nTurns, iArmed,
	                                 (int)kIf.pPreGame->activePlayer()));
	kResult.steps.push_back(HHFormat("NetMessageHandler.Response* x%u", (unsigned)HHDeliverNetMessages(kIf)));
	kIf.pUI->ClearShownNotifications();   // NotificationPanel OnSetActivePlayer: RemoveNotificationID for all
	kIf.pUI->TakeNotificationAdded();
	kIf.pUI->ClearSelection();            // the engine drops the selection when the active player changes
	kIf.pUI->TakeActivePlayerTurnStart();
	if(iArmed != nTurns)
	{
		kResult.error = "SetAIAutoPlay did not arm autoplay";
		return false;
	}
	// CONTINUE (vpbench.CONTINUE, LoadScreen context): Events.LoadScreenClose() (to the UI contexts' listeners),
	// Game.SetPausePlayer(-1) unpauses the game core, UI.SetDontShowPopups(false) is UI.
	kResult.steps.push_back(HHFormat("Events.LoadScreenClose(): %d listener(s)", HHFireUIEvent(kScripts, "LoadScreenClose", std::string())));
	HH_STEP("Game.SetPausePlayer(-1)", kIf.pGame->SetPausePlayer(NO_PLAYER));
	HHDeliverNetMessages(kIf);

	// The game core: ICvGame1::Update until the autoplay counter is back to 0 nTurns turns later (the
	// engine calls it once per frame from its core thread; one call processes one player).
	int iTurn = kIf.pGame->GetGameTurn();
	const int iEndTurn = iTurn + nTurns;
	kResult.iStartTurn = iTurn;
	double tTurn = Now();
	// M6 tooling: turn-times.jsonl as vpbench writes it (seconds since autoplay started, here exact).
	const double tStart = tTurn;
	const std::string turnTimes = HHGetPaths().run + "turn-times.jsonl";
	DeleteFileW(HHWiden(turnTimes).c_str());
	if(!o.checkpointLua.empty())
		HHEnsureDirectory(HHGetPaths().run + "checkpoints");
	int iTurnUpdates = 0;
	int iIdle = -1;
	const int kMaxUpdatesPerTurn = 2000;
	// A game loaded from a save of an ended game is in extended play (CvGame's Read makes GAMESTATE_OVER that), and plays on:
	// autoplay stops when the state changes.
	const int iStartState = (int)kIf.pGame->GetGameState();
	for(;;)
	{
		// --frame-skew K: one extra Update (an engine frame without the UI frame) before every K-th one, so the
		// turn slice (+1 per Update) advances differently. Results must not depend on it (frame-count dependence).
		if(o.frameSkew > 0 && kResult.iUpdates % o.frameSkew == o.frameSkew - 1)
		{
			kIf.pEngine->SetInGameCoreUpdate(true);
			kIf.pGame->Update();
			kIf.pEngine->SetInGameCoreUpdate(false);
			kIf.pEngine->EndOfUpdate();
			HHDeliverNetMessages(kIf);
		}
		kIf.pEngine->SetInGameCoreUpdate(true);
		kIf.pGame->Update();
		kIf.pEngine->SetInGameCoreUpdate(false);
		kIf.pEngine->EndOfUpdate();
		HHDeliverNetMessages(kIf);
		// Slots of players that die or live again: HHEngineUtility::closeSlot/openSlot (CvPlayer::setAlive).
		if(!HHGetOptions().noUiFrame)
		{
			NotificationAdded(kIf);
			HHGetProfiles().RunHook(kScripts, "ui-frame", NULL);
			ShowQueuedPopups(kIf, kResult);
			HumanTurnFrame(kIf);
		}
		++kResult.iUpdates;
		++iTurnUpdates;
		const int iNow = kIf.pGame->GetGameTurn();
		if(iNow != iTurn)
		{
			kResult.turnSeconds.push_back(std::make_pair(iTurn, Now() - tTurn));
			kResult.turnUpdates.push_back(std::make_pair(iTurn, iTurnUpdates));
			HHLog("turn %d: %d updates, %.2f s", iTurn, iTurnUpdates, Now() - tTurn);
			iTurn = iNow;
			tTurn = Now();
			iTurnUpdates = 0;
			AppendLine(turnTimes, HHFormat("{\"turn\": %d, \"seconds\": %.3f}", iNow, tTurn - tStart));
			if(!o.checkpointLua.empty() && o.checkpointEvery > 0 && iNow % o.checkpointEvery == 0)
				WriteCheckpoint(kScripts, iNow, "", tTurn - tStart);
		}
		if((int)kIf.pGame->GetGameState() != iStartState)
		{
			HHLog("game state %d at turn %d", (int)kIf.pGame->GetGameState(), iNow);
			break;
		}
		if(iIdle < 0 && iNow >= iEndTurn && LuaInt(kScripts, "return Game.GetAIAutoPlay()", -1) == 0)
			iIdle = 0;   // autoplay ended: the human turn; the reference keeps running Updates until its end probe
		if(iIdle >= 0 && iIdle++ >= nIdleUpdates)
			break;
		if(iTurnUpdates > kMaxUpdatesPerTurn)
		{
			kResult.error = HHFormat("watchdog: turn %d did not end after %d updates", iNow, iTurnUpdates);
			HHLogError("%s", kResult.error.c_str());
			break;
		}
	}
	kResult.turnSeconds.push_back(std::make_pair(iTurn, Now() - tTurn));
	kResult.turnUpdates.push_back(std::make_pair(iTurn, iTurnUpdates));
	kResult.iTurnsPlayed = kIf.pGame->GetGameTurn();
	kResult.netMessages = kIf.pEngine->GetNetMessageCounts();
	kResult.timings.push_back(std::make_pair(std::string("autoplay"), Now() - t0));
	// M6 tooling: the final checkpoint (vpbench's turn-NNNN-final.json), after the idle updates.
	if(!o.checkpointLua.empty())
		WriteCheckpoint(kScripts, kIf.pGame->GetGameTurn(), "-final", Now() - tStart);
	if(!o.luaBeforeDump.empty())
	{
		const std::string json = RunFileToJson(kScripts, o.luaBeforeDump, "--lua-before-dump");
		HHWriteTextFile(HHGetPaths().run + "lua-before-dump.json", (json.empty() ? std::string("null") : json) + "\n");
		kResult.steps.push_back(HHFormat("--lua-before-dump %s -> %s", o.luaBeforeDump.c_str(),
		                                 json.size() > 200 ? (json.substr(0, 200) + "...").c_str() : json.c_str()));
	}
	bool bDumped = true;
	if(o.noDumps)
		kResult.steps.push_back("--no-dumps: no final dump");
	else
	{
		const std::string folder = HHGetPaths().run + HHFormat("dump-t%03d", kResult.iTurnsPlayed);
		bDumped = !DumpState(kScripts, folder).empty();
		kResult.steps.push_back("Game.DumpHeadlessState(" + folder + ")");
	}
	// The final save (unless --no-save-final): the game on its last turn, taken after the final checkpoint, Lua and
	// dump have read it, so none of them depends on it.
	if(o.saveFinal)
		HHSaveFinal();
	kResult.bAutoplayOk = kResult.error.empty() && bDumped;
	return kResult.bAutoplayOk;
}
