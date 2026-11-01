#include "lua/HHLuaGlobals.h"
#include "lua/HHLuaEnv.h"
#include "lua/HHScriptSystem.h"
#include "setup/HHVfs.h"
#include "setup/HHSaveGame.h"
#include "engine/HHRuntime.h"

namespace
{
	//--------------------------------------------------------------------------
	// Locale
	//--------------------------------------------------------------------------
	// Locale.Lookup(key, ...) / Locale.ConvertTextKey(key, ...): CvLocalization lookup with the
	// extra arguments pushed as {1}, {2}, ... Numbers that are integral are pushed as ints.
	int HHLuaLocaleLookup(lua_State* L)
	{
		const char* szKey = luaL_checkstring(L, 1);
		Localization::String str = Localization::Lookup(szKey);
		const int n = lua_gettop(L);
		for(int i = 2; i <= n; ++i)
		{
			switch(lua_type(L, i))
			{
			case LUA_TNUMBER:
			{
				const lua_Number d = lua_tonumber(L, i);
				if((lua_Number)(int)d == d)
					str.PushArgument((int)d);
				else
					str.PushArgument((double)d);
				break;
			}
			case LUA_TBOOLEAN:
				str.PushArgument(lua_toboolean(L, i) ? 1 : 0);
				break;
			case LUA_TSTRING:
			{
				size_t len = 0;
				const char* s = lua_tolstring(L, i, &len);
				str.PushArgument(s, len);
				break;
			}
			default:
				str.PushArgument("");
				break;
			}
		}
		size_t bytes = 0;
		const char* szText = str.toUTF8(bytes);
		if(szText)
			lua_pushlstring(L, szText, bytes);
		else
			lua_pushstring(L, szKey);
		return 1;
	}

	int HHLuaLocaleHasTextKey(lua_State* L)
	{
		const char* szKey = luaL_checkstring(L, 1);
		Localization::String str = Localization::Lookup(szKey);
		const char* szText = str.toUTF8();
		// ASSUMPTION: a missing key looks up to the key itself (or nothing).
		lua_pushboolean(L, szText && *szText && strcmp(szText, szKey) != 0);
		return 1;
	}

	// __HHGameEventsChanged(): the prelude has changed a GameEvents listener list.
	int HHLuaGameEventsChanged(lua_State* L)
	{
		static_cast<HHScriptSystem*>(lua_touserdata(L, lua_upvalueindex(1)))->GameEventListenersChanged();
		return 0;
	}

	// UI.IsLoadedGame(): true once a save has been loaded.
	int HHLuaIsLoadedGame(lua_State* L)
	{
		lua_pushboolean(L, HHIsLoadedGame());
		return 1;
	}

	// UI.SaveGame(name): the in-game Save menu's call, a save named name in the run's save folder.
	int HHLuaSaveGame(lua_State* L)
	{
		const char* szName = lua_tostring(L, 1);
		lua_pushboolean(L, szName && HHSaveNamed(szName));
		return 1;
	}

	//--------------------------------------------------------------------------
	// Modding
	//--------------------------------------------------------------------------
	HHVfs* GetVfs(lua_State* L)
	{
		return (HHVfs*)lua_touserdata(L, lua_upvalueindex(1));
	}

	// Modding.GetActivatedMods(): array of {ID = guid, Version = n}, in activation order.
	int HHLuaModdingGetActivatedMods(lua_State* L)
	{
		const std::vector<HHActivatedMod>& mods = GetVfs(L)->GetMods();
		lua_createtable(L, (int)mods.size(), 0);
		for(size_t i = 0; i < mods.size(); ++i)
		{
			lua_createtable(L, 0, 2);
			lua_pushstring(L, mods[i].id.c_str());
			lua_setfield(L, -2, "ID");
			lua_pushinteger(L, mods[i].version);
			lua_setfield(L, -2, "Version");
			lua_rawseti(L, -2, (int)i + 1);
		}
		return 1;
	}

	int HHLuaEntryPointIterator(lua_State* L)
	{
		// upvalue 1: array of entries, upvalue 2: next index
		const int i = (int)lua_tointeger(L, lua_upvalueindex(2));
		lua_rawgeti(L, lua_upvalueindex(1), i);
		if(lua_isnil(L, -1))
			return 1;
		lua_pushinteger(L, i + 1);
		lua_replace(L, lua_upvalueindex(2));
		return 1;
	}

	// Modding.GetActivatedModEntryPoints(type): iterator over {ModID, Version, Name,
	// Description, Type, File} of every activated mod, in activation order.
	int HHLuaModdingGetActivatedModEntryPoints(lua_State* L)
	{
		const char* szType = luaL_checkstring(L, 1);
		const std::vector<HHActivatedMod>& mods = GetVfs(L)->GetMods();
		lua_newtable(L);
		int n = 0;
		for(size_t i = 0; i < mods.size(); ++i)
			for(size_t j = 0; j < mods[i].entryPoints.size(); ++j)
			{
				const HHModEntryPoint& e = mods[i].entryPoints[j];
				if(e.type != szType)
					continue;
				lua_createtable(L, 0, 6);
				lua_pushstring(L, mods[i].id.c_str()); lua_setfield(L, -2, "ModID");
				lua_pushinteger(L, mods[i].version); lua_setfield(L, -2, "Version");
				lua_pushstring(L, e.name.c_str()); lua_setfield(L, -2, "Name");
				lua_pushstring(L, e.description.c_str()); lua_setfield(L, -2, "Description");
				lua_pushstring(L, e.type.c_str()); lua_setfield(L, -2, "Type");
				lua_pushstring(L, e.file.c_str()); lua_setfield(L, -2, "File");
				lua_rawseti(L, -2, ++n);
			}
		lua_pushinteger(L, 1);
		lua_pushcclosure(L, HHLuaEntryPointIterator, 2);
		return 1;
	}

	const HHActivatedMod* FindMod(lua_State* L, int iIdArg)
	{
		const char* szId = luaL_checkstring(L, iIdArg);
		const int iVersion = lua_isnumber(L, iIdArg + 1) ? (int)lua_tointeger(L, iIdArg + 1) : -1;
		const std::vector<HHActivatedMod>& mods = GetVfs(L)->GetMods();
		for(size_t i = 0; i < mods.size(); ++i)
			if(_stricmp(mods[i].id.c_str(), szId) == 0 && (iVersion < 0 || mods[i].version == iVersion))
				return &mods[i];
		return NULL;
	}

	// Modding.GetModProperty(id, version, name)
	int HHLuaModdingGetModProperty(lua_State* L)
	{
		const HHActivatedMod* pMod = FindMod(L, 1);
		const char* szName = luaL_checkstring(L, 3);
		if(pMod)
			for(size_t i = 0; i < pMod->properties.size(); ++i)
				if(pMod->properties[i].first == szName)
				{
					lua_pushstring(L, pMod->properties[i].second.c_str());
					return 1;
				}
		return 0;
	}

	// Modding.IsModActivated(id[, version])
	int HHLuaModdingIsModActivated(lua_State* L)
	{
		lua_pushboolean(L, FindMod(L, 1) != NULL);
		return 1;
	}

	// Modding.GetEvaluatedFilePath(id, version, file) -> {EvaluatedPath = absolute}
	int HHLuaModdingGetEvaluatedFilePath(lua_State* L)
	{
		const HHActivatedMod* pMod = FindMod(L, 1);
		const char* szFile = luaL_checkstring(L, 3);
		if(!pMod || pMod->folder.empty())
			return 0;
		std::string path = pMod->folder + "\\" + szFile;
		for(size_t i = 0; i < path.size(); ++i)
			if(path[i] == '/')
				path[i] = '\\';
		lua_createtable(L, 0, 1);
		lua_pushstring(L, path.c_str());
		lua_setfield(L, -2, "EvaluatedPath");
		return 1;
	}

	//--------------------------------------------------------------------------
	// PreGame (read-only subset over ICvPreGame1)
	//--------------------------------------------------------------------------
	ICvPreGame1* GetPreGame(lua_State* L)
	{
		return (ICvPreGame1*)lua_touserdata(L, lua_upvalueindex(1));
	}

	PlayerTypes ArgPlayer(lua_State* L)
	{
		return (PlayerTypes)luaL_checkint(L, 1);
	}

	void PushCvString(lua_State* L, const CvString& s)
	{
		lua_pushlstring(L, s.c_str(), s.size());
	}

#define HH_PREGAME_INT0(fn, expr) int fn(lua_State* L) { ICvPreGame1* p = GetPreGame(L); lua_pushinteger(L, (int)(expr)); return 1; }
#define HH_PREGAME_BOOL0(fn, expr) int fn(lua_State* L) { ICvPreGame1* p = GetPreGame(L); lua_pushboolean(L, (expr) ? 1 : 0); return 1; }
#define HH_PREGAME_INT1(fn, expr) int fn(lua_State* L) { ICvPreGame1* p = GetPreGame(L); PlayerTypes e = ArgPlayer(L); lua_pushinteger(L, (int)(expr)); return 1; }
#define HH_PREGAME_BOOL1(fn, expr) int fn(lua_State* L) { ICvPreGame1* p = GetPreGame(L); PlayerTypes e = ArgPlayer(L); lua_pushboolean(L, (expr) ? 1 : 0); return 1; }
#define HH_PREGAME_STR1(fn, expr) int fn(lua_State* L) { ICvPreGame1* p = GetPreGame(L); PlayerTypes e = ArgPlayer(L); PushCvString(L, expr); return 1; }

	HH_PREGAME_INT0(PGGetWorldSize, p->worldSize())
	HH_PREGAME_INT0(PGGetGameSpeed, p->gameSpeed())
	HH_PREGAME_INT0(PGGetEra, p->era())
	HH_PREGAME_INT0(PGGetNumMinorCivs, p->numMinorCivs())
	HH_PREGAME_INT0(PGGetMaxTurns, p->maxTurns())
	HH_PREGAME_INT0(PGGetGameType, p->gameType())
	HH_PREGAME_INT0(PGGetActivePlayer, p->activePlayer())
	HH_PREGAME_INT0(PGGetClimate, p->climate())
	HH_PREGAME_INT0(PGGetSeaLevel, p->seaLevel())
	HH_PREGAME_BOOL0(PGGetLoadWBScenario, p->loadWBScenario())
	HH_PREGAME_BOOL0(PGIsRandomMapScript, p->randomMapScript())
	HH_PREGAME_BOOL0(PGIsRandomWorldSize, p->randomWorldSize())
	HH_PREGAME_BOOL0(PGIsMultiplayerGame, p->isNetworkMultiplayerGame())
	HH_PREGAME_BOOL0(PGIsHotSeatGame, p->isHotSeatGame())
	HH_PREGAME_BOOL0(PGIsInternetGame, p->isInternetGame())
	HH_PREGAME_BOOL0(PGGameStarted, p->gameStarted())
	HH_PREGAME_INT1(PGGetCivilization, p->civilization(e))
	HH_PREGAME_INT1(PGGetLeaderType, p->leaderHead(e))
	HH_PREGAME_INT1(PGGetSlotStatus, p->slotStatus(e))
	HH_PREGAME_INT1(PGGetSlotClaim, p->slotClaim(e))
	HH_PREGAME_INT1(PGGetTeam, p->teamType(e))
	HH_PREGAME_INT1(PGGetHandicap, p->handicap(e))
	HH_PREGAME_INT1(PGGetCivilizationColor, p->playerColor(e))
	HH_PREGAME_INT1(PGGetMinorCivType, p->minorCivType(e))
	HH_PREGAME_BOOL1(PGIsHuman, p->isHuman(e))
	HH_PREGAME_BOOL1(PGIsMinorCiv, p->isMinorCiv(e))
	HH_PREGAME_STR1(PGGetLeaderName, p->leaderName(e))
	HH_PREGAME_STR1(PGGetCivilizationDescription, p->civilizationDescription(e))
	HH_PREGAME_STR1(PGGetCivilizationShortDescription, p->civilizationShortDescription(e))
	HH_PREGAME_STR1(PGGetCivilizationAdjective, p->civilizationAdjective(e))
	HH_PREGAME_STR1(PGGetNickName, p->nickname(e))

	int PGGetMapScript(lua_State* L) { PushCvString(L, GetPreGame(L)->mapScriptName()); return 1; }
	int PGIsVictory(lua_State* L) { lua_pushboolean(L, GetPreGame(L)->isVictory((VictoryTypes)luaL_checkint(L, 1)) ? 1 : 0); return 1; }

	int PGGetMapOption(lua_State* L)
	{
		const char* szName = luaL_checkstring(L, 1);
		int iValue = 0;
		if(GetPreGame(L)->GetMapOption(szName, iValue))
		{
			lua_pushinteger(L, iValue);
			return 1;
		}
		return 0;
	}

	int PGGetGameOption(lua_State* L)
	{
		int iValue = 0;
		bool bFound = lua_type(L, 1) == LUA_TNUMBER ? GetPreGame(L)->GetGameOption((GameOptionTypes)lua_tointeger(L, 1), iValue)
		                                            : GetPreGame(L)->GetGameOption(luaL_checkstring(L, 1), iValue);
		if(bFound)
		{
			lua_pushinteger(L, iValue);
			return 1;
		}
		return 0;
	}

	const luaL_Reg kPreGame[] =
	{
		{ "GetWorldSize", PGGetWorldSize },
		{ "GetGameSpeed", PGGetGameSpeed },
		{ "GetEra", PGGetEra },
		{ "GetNumMinorCivs", PGGetNumMinorCivs },
		{ "GetMaxTurns", PGGetMaxTurns },
		{ "GetGameType", PGGetGameType },
		{ "GetActivePlayer", PGGetActivePlayer },
		{ "GetClimate", PGGetClimate },
		{ "GetSeaLevel", PGGetSeaLevel },
		{ "GetLoadWBScenario", PGGetLoadWBScenario },
		{ "IsRandomMapScript", PGIsRandomMapScript },
		{ "IsRandomWorldSize", PGIsRandomWorldSize },
		{ "IsMultiplayerGame", PGIsMultiplayerGame },
		{ "IsHotSeatGame", PGIsHotSeatGame },
		{ "IsInternetGame", PGIsInternetGame },
		{ "GameStarted", PGGameStarted },
		{ "GetCivilization", PGGetCivilization },
		{ "GetLeaderType", PGGetLeaderType },
		{ "GetSlotStatus", PGGetSlotStatus },
		{ "GetSlotClaim", PGGetSlotClaim },
		{ "GetTeam", PGGetTeam },
		{ "GetHandicap", PGGetHandicap },
		{ "GetCivilizationColor", PGGetCivilizationColor },
		{ "GetMinorCivType", PGGetMinorCivType },
		{ "IsHuman", PGIsHuman },
		{ "IsMinorCiv", PGIsMinorCiv },
		{ "GetLeaderName", PGGetLeaderName },
		{ "GetCivilizationDescription", PGGetCivilizationDescription },
		{ "GetCivilizationShortDescription", PGGetCivilizationShortDescription },
		{ "GetCivilizationAdjective", PGGetCivilizationAdjective },
		{ "GetNickName", PGGetNickName },
		{ "GetMapScript", PGGetMapScript },
		{ "IsVictory", PGIsVictory },
		{ "GetMapOption", PGGetMapOption },
		{ "GetGameOption", PGGetGameOption },
		{ NULL, NULL }
	};

	void RegisterWithUpvalue(lua_State* L, const char* szTable, const luaL_Reg* pFuncs, void* pUpvalue)
	{
		lua_newtable(L);
		for(const luaL_Reg* r = pFuncs; r->name; ++r)
		{
			lua_pushlightuserdata(L, pUpvalue);
			lua_pushcclosure(L, r->func, 1);
			lua_setfield(L, -2, r->name);
		}
		lua_setglobal(L, szTable);
	}

	// Lua-side parts: Events/LuaEvents proxies, the simple Locale helpers.
	const char* kLuaPrelude =
		"-- Listener registrations per Lua context (StateName of the registering code), for the run report:\n"
		"-- __HHContextRecords[context][kind][event] = count, kind Events, LuaEvents or GameEvents.\n"
		"__HHContextRecords = {}\n"
		"local function recordListener(kind, name, env)\n"
		"  local ctx = type(env) == 'table' and rawget(env, 'StateName') or nil\n"
		"  if type(ctx) ~= 'string' then ctx = 'Main' end\n"
		"  local r = __HHContextRecords[ctx] or {}\n"
		"  __HHContextRecords[ctx] = r\n"
		"  r[kind] = r[kind] or {}\n"
		"  r[kind][name] = (r[kind][name] or 0) + 1\n"
		"end\n"
		"-- The environment of the code that called the caller of this function (pcall: getfenv raises at a tail call).\n"
		"local function callerEnv() local ok, env = pcall(getfenv, 4) return ok and env or nil end\n"
		"local function reportError(err, where)\n"
		"  if __HHReportError then __HHReportError(tostring(err), where) end\n"
		"end\n"
		"local function eventTable(dispatch, kind)\n"
		"  return setmetatable({}, {__index = function(t, name)\n"
		"    local listeners = {}\n"
		"    local ev = {}\n"
		"    -- Add/Remove accept both Events.X.Add(f) and Events.X:Add(f).\n"
		"    ev.Add = function(a, b) local f = (a == ev) and b or a; listeners[#listeners + 1] = f; recordListener(kind, name, callerEnv()) end\n"
		"    ev.Remove = function(a, b) local f = (a == ev) and b or a\n"
		"      for i = #listeners, 1, -1 do if listeners[i] == f then table.remove(listeners, i) end end end\n"
		"    ev.RemoveAll = function() listeners = {} end\n"
		"    ev.Count = function() return #listeners end\n"
		"    -- The host fires the engine events it emulates (Events) through __HHFireEvent: every listener, errors logged.\n"
		"    ev.__HHFire = function(...)\n"
		"      local copy = {} for i = 1, #listeners do copy[i] = listeners[i] end\n"
		"      for i = 1, #copy do\n"
		"        local ok, r = pcall(copy[i], ...)\n"
		"        if not ok then print('Runtime Error: ' .. tostring(r)); reportError(r, kind .. '.' .. name) end\n"
		"      end\n"
		"      return #copy\n"
		"    end\n"
		"    setmetatable(ev, {__call = function(self, ...)\n"
		"      if dispatch then for _, f in ipairs(listeners) do f(...) end end\n"
		"    end})\n"
		"    rawset(t, name, ev)\n"
		"    return ev\n"
		"  end})\n"
		"end\n"
		"-- Events: engine (UI/graphics) events; calling a member is a no-op. Listeners are kept; the host fires only the\n"
		"-- events it emulates (__HHFireEvent).\n"
		"Events = eventTable(false, 'Events')\n"
		"-- LuaEvents: script-to-script events; dispatched to listeners.\n"
		"LuaEvents = eventTable(true, 'LuaEvents')\n"
		"function __HHFireEvent(name, ...)\n"
		"  local ev = rawget(Events, name)\n"
		"  if ev == nil then return 0 end\n"
		"  return ev.__HHFire(...)\n"
		"end\n"
		"-- GameEvents: the DLL's hooks (ICvEngineScriptSystem1::Call*), measured semantics (contract 10.1):\n"
		"-- every listener runs, in registration order; a raising listener is logged and does not vote;\n"
		"-- handled = at least one listener. __HHGameEventListeners[name] is the listener list.\n"
		"__HHGameEventListeners = {}\n"
		"__HHGameEventsChanged()\n"
		"local function gameEventCall(name, mode, init, ...)\n"
		"  local list = __HHGameEventListeners[name]\n"
		"  if list == nil or #list == 0 then return false, init end\n"
		"  local copy = {} for i = 1, #list do copy[i] = list[i] end\n"
		"  local value = init\n"
		"  if mode == 'testall' then value = true elseif mode == 'testany' then value = false elseif mode == 'hook' then value = false end\n"
		"  for i = 1, #copy do\n"
		"    local ok, r = pcall(copy[i], ...)\n"
		"    if not ok then print('Runtime Error: ' .. tostring(r)); reportError(r, 'GameEvents.' .. name)\n"
		"    elseif mode == 'testall' then if not r then value = false end\n"
		"    elseif mode == 'testany' then if r then value = true end\n"
		"    elseif mode == 'accum' then if type(r) == 'number' then value = value + r end\n"
		"    end\n"
		"  end\n"
		"  return true, value\n"
		"end\n"
		"__HHGameEventDispatch = gameEventCall\n"
		"GameEvents = setmetatable({}, {__index = function(t, name)\n"
		"  if type(name) ~= 'string' then return nil end\n"
		"  __HHGameEventListeners[name] = __HHGameEventListeners[name] or {}\n"
		"  local ev = {}\n"
		"  -- Add/Remove accept both GameEvents.X.Add(f) and GameEvents.X:Add(f).\n"
		"  ev.Add = function(a, b) local f = (a == ev) and b or a; local l = __HHGameEventListeners[name]; l[#l + 1] = f; __HHGameEventsChanged()\n"
		"    recordListener('GameEvents', name, callerEnv()) end\n"
		"  ev.Remove = function(a, b) local f = (a == ev) and b or a; local l = __HHGameEventListeners[name]\n"
		"    for i = #l, 1, -1 do if l[i] == f then table.remove(l, i) end end; __HHGameEventsChanged() end\n"
		"  ev.RemoveAll = function() __HHGameEventListeners[name] = {}; __HHGameEventsChanged() end\n"
		"  ev.Count = function() return #__HHGameEventListeners[name] end\n"
		"  ev.Call = function(...) local h, v = gameEventCall(name, 'hook', false, ...) return v end\n"
		"  setmetatable(ev, {__call = function(self, ...) return ev.Call(...) end})\n"
		"  rawset(t, name, ev)\n"
		"  return ev\n"
		"end})\n"
		"-- Engine enum tables the gameplay add-ins use (CvEnums.h).\n"
		"SlotStatus = SlotStatus or {SS_OPEN = 0, SS_COMPUTER = 1, SS_CLOSED = 2, SS_TAKEN = 3, SS_OBSERVER = 4}\n"
		"MapModData = MapModData or {}\n"
		"-- Firaxis extensions of the standard libraries used by map scripts.\n"
		"-- table.fill(value, n) -> {value x n} (1-based); table.fill(t, value, n) fills t[1..n] and returns t.\n"
		"-- ASSUMPTION (trace): semantics inferred from the scripts (MapGenerator: 'incoming plot tables are indexed from 1').\n"
		"table.fill = table.fill or function(a, b, c)\n"
		"  if type(a) == 'table' and c ~= nil then for i = 1, c do a[i] = b end return a end\n"
		"  local t = {} for i = 1, b do t[i] = a end return t\n"
		"end\n"
		"math.clamp = math.clamp or function(x, lo, hi) if x < lo then return lo elseif x > hi then return hi end return x end\n"
		"-- DB.CreateQuery(sql) -> callable(...) returning the row iterator of DB.Query(sql, ...).\n"
		"DB.CreateQuery = DB.CreateQuery or function(sql) return function(...) return DB.Query(sql, ...) end end\n"
		"Locale.IsNilOrWhitespace = function(s) return s == nil or tostring(s):match('^%s*$') ~= nil end\n"
		"Locale.IsNilOrEmpty = function(s) return s == nil or s == '' end\n"
		"Locale.ToUpper = function(s) return string.upper(tostring(s)) end\n"
		"Locale.ToLower = function(s) return string.lower(tostring(s)) end\n"
		"Locale.Length = function(s) return string.len(tostring(s)) end\n"
		"Locale.Compare = function(a, b) if a == b then return 0 elseif a < b then return -1 else return 1 end end\n"
		"Locale.ToNumber = function(n, fmt) return tostring(n) end\n"
		"Locale.GetCurrentLanguage = function() return {Type = 'en_US'} end\n"
		"-- Modding.OpenUserData(id, version): a mod's saved settings. Headless they start empty (every mod's defaults) and live\n"
		"-- in memory for the run: the game keeps them in the user's cache, which a run must not depend on. Modding.OpenSaveData()\n"
		"-- is the C function on the run's save database (HHLuaEnv.h), which saves embed and loads restore.\n"
		"local userData = {}\n"
		"local function store(key)\n"
		"  if userData[key] == nil then\n"
		"    local values = {}\n"
		"    -- Query(sql): no tables, so every query gives no rows.\n"
		"    userData[key] = {GetValue = function(self, k) return values[k] end, SetValue = function(self, k, v) values[k] = v end,\n"
		"                     Query = function() return function() return nil end end}\n"
		"  end\n"
		"  return userData[key]\n"
		"end\n"
		"Modding.OpenUserData = Modding.OpenUserData or function(id, version) return store(tostring(id) .. '|' .. tostring(version)) end\n";

	// Stub UI contexts (InGameUIAddin scripts, UI emulation profiles, Lua probes): each context is a Lua thread whose
	// environment gets the UI-only globals the game gives a UI context. Nothing is shown, so they absorb every call:
	// - Controls, Mouse, Keys, KeyEvents, MouseEvents, PopupPriority: the chainable stub (any member, any call, gives
	//   the stub again; arithmetic gives 0, concatenation and tostring give '');
	// - ContextPtr: per context; SetHide/IsHidden keep the hidden flag (initial: the XML Context's Hidden attribute),
	//   IsHotLoad is false, GetID the context name, handlers are stored and never called, instance tables of
	//   BuildInstanceForControl answer every control with the stub;
	// - UIManager: stub calls, a 1024x768 screen, nothing modal;
	// - UI, Network, Steam, OptionsManager, Matchmaking: every member is a function returning nil (nothing selected,
	//   nothing sent), except UI.RemoveNotification, the engine's dismiss (host: __HHUIRemoveNotification);
	// - ToHexFromGrid, ToGridFromHex, HexToWorld, WorldToHex: the stub; Path: the string helpers.
	// Game, Map, Players, Teams, GameInfo, Events, LuaEvents and GameEvents are the shared ones.
	const char* kUIContextLua =
		"local stubMt = {}\n"
		"local stub = setmetatable({}, stubMt)\n"
		"local function zero() return 0 end\n"
		"local function str(x) local t = type(x) if t == 'string' or t == 'number' then return x end return '' end\n"
		"-- Instance builders (ContextPtr:BuildInstanceForControl, pulldown:BuildEntry) fill the caller's table: every control the stub.\n"
		"local function fill(self, name, inst)\n"
		"  if type(inst) == 'table' and getmetatable(inst) == nil then setmetatable(inst, {__index = function() return stub end}) end\n"
		"  return stub\n"
		"end\n"
		"stubMt.__index = function(t, k) if k == 'BuildEntry' or k == 'BuildInstanceForControl' or k == 'BuildInstance' then return fill end return stub end\n"
		"stubMt.__newindex = function() end\n"
		"stubMt.__call = function() return stub end\n"
		"stubMt.__add, stubMt.__sub, stubMt.__mul, stubMt.__div, stubMt.__mod, stubMt.__pow, stubMt.__unm = zero, zero, zero, zero, zero, zero, zero\n"
		"stubMt.__concat = function(a, b) return str(a) .. str(b) end\n"
		"stubMt.__tostring = function() return '' end\n"
		"stubMt.__lt = function() return false end\n"
		"stubMt.__le = function() return false end\n"
		"__HHUIStub = stub\n"
		"local function nilFn() return nil end\n"
		"local function stubFn() return stub end\n"
		"local function nilApi() return setmetatable({}, {__index = function() return nilFn end}) end\n"
		"local function stubApi() return setmetatable({}, {__index = function() return stubFn end}) end\n"
		"local function ext(p) return string.match(p or '', '(%.[^%.\\\\/]*)$') or '' end\n"
		"local function base(p) return string.match(p or '', '([^\\\\/]*)$') or '' end\n"
		"local path = nilApi()\n"
		"path.GetExtension = function(p) return ext(p) end\n"
		"path.GetFileName = function(p) return base(p) end\n"
		"path.GetFileNameWithoutExtension = function(p) local b = base(p) return string.sub(b, 1, #b - #ext(b)) end\n"
		"path.GetDirectoryName = function(p) return string.match(p or '', '^(.*)[\\\\/][^\\\\/]*$') or '' end\n"
		"__HHUIContextStates = {}\n"
		"function __HHMakeUIContext(env, name, hidden)\n"
		"  local state = {hidden = hidden and true or false}\n"
		"  __HHUIContextStates[name] = state\n"
		"  -- A LuaContext control for code that checks control kinds (CPK.UI.Control.IsKind walks CTypeName/CBaseType).\n"
		"  -- Unknown methods (capitalised names) are stubs; other keys are nil, so code can keep its own fields on it.\n"
		"  local ctx = setmetatable({}, {__index = function(t, k) if type(k) == 'string' and string.find(k, '^%u') then return stubFn end end,\n"
		"                                CTypeName = 'LuaContext', CBaseType = {CTypeName = 'ControlBase'}})\n"
		"  ctx.SetHide = function(self, b) state.hidden = b and true or false end\n"
		"  ctx.IsHidden = function() return state.hidden end\n"
		"  ctx.IsHotLoad = function() return false end\n"
		"  ctx.GetID = function() return name end\n"
		"  ctx.SetShowHideHandler = function(self, f) state.showHide = f end\n"
		"  ctx.SetInputHandler = function(self, f) state.input = f end\n"
		"  ctx.SetUpdate = function(self, f) state.update = f end\n"
		"  ctx.ClearUpdate = function() state.update = nil end\n"
		"  ctx.BuildInstanceForControl = fill\n"
		"  env.ContextPtr = ctx\n"
		"  env.Controls = stub\n"
		"  local ui = nilApi()\n"
		"  ui.RemoveNotification = function(id) if __HHUIRemoveNotification then return __HHUIRemoveNotification(id) end end\n"
		"  ui.IsLoadedGame = function() return __HHIsLoadedGame() end\n"
		"  ui.SaveGame = function(name) return __HHSaveGame(name) end\n"
		"  env.UI = ui\n"
		"  local uim = stubApi()\n"
		"  uim.GetScreenSizeVal = function() return 1024, 768 end\n"
		"  uim.GetMousePos = function() return 0, 0 end\n"
		"  uim.IsModal = function() return false end\n"
		"  env.UIManager = uim\n"
		"  env.Network, env.Steam, env.OptionsManager, env.Matchmaking = nilApi(), nilApi(), nilApi(), nilApi()\n"
		"  env.Mouse, env.Keys, env.KeyEvents, env.MouseEvents, env.PopupPriority = stub, stub, stub, stub, stub\n"
		"  env.ToHexFromGrid, env.ToGridFromHex, env.HexToWorld, env.WorldToHex = stubFn, stubFn, stubFn, stubFn\n"
		"  env.GridToWorld = function() return 0, 0, 0 end\n"
		"  env.WorldToGrid = function() return 0, 0 end\n"
		"  -- Every content package (the expansions, DLC) counts as active, as in a BNW game.\n"
		"  env.ContentManager = setmetatable({IsActive = function() return true end}, {__index = function() return nilFn end})\n"
		"  env.ContentType = stub\n"
		"  env.Path = path\n"
		"end\n";
}

bool HHLuaRegisterEngineGlobals(HHScriptSystem& kScripts, HHVfs& kVfs, ICvPreGame1* pPreGame)
{
	lua_State* L = kScripts.GetMainState();

	lua_newtable(L);
	lua_pushcfunction(L, HHLuaLocaleLookup);
	lua_setfield(L, -2, "Lookup");
	lua_pushcfunction(L, HHLuaLocaleLookup);
	lua_setfield(L, -2, "ConvertTextKey");
	lua_pushcfunction(L, HHLuaLocaleHasTextKey);
	lua_setfield(L, -2, "HasTextKey");
	lua_setglobal(L, "Locale");

	static const luaL_Reg kModding[] =
	{
		{ "GetActivatedMods", HHLuaModdingGetActivatedMods },
		{ "GetActivatedModEntryPoints", HHLuaModdingGetActivatedModEntryPoints },
		{ "GetModProperty", HHLuaModdingGetModProperty },
		{ "IsModActivated", HHLuaModdingIsModActivated },
		{ "GetEvaluatedFilePath", HHLuaModdingGetEvaluatedFilePath },
		{ "OpenSaveData", HHLuaModdingOpenSaveData },
		{ NULL, NULL }
	};
	RegisterWithUpvalue(L, "Modding", kModding, &kVfs);
	lua_pushlightuserdata(L, &kScripts);
	lua_pushcclosure(L, HHLuaGameEventsChanged, 1);
	lua_setglobal(L, "__HHGameEventsChanged");
	// UI.IsLoadedGame of the stub UI contexts (kUIContextLua).
	lua_pushcfunction(L, HHLuaIsLoadedGame);
	lua_setglobal(L, "__HHIsLoadedGame");
	lua_pushcfunction(L, HHLuaSaveGame);
	lua_setglobal(L, "__HHSaveGame");
	if(pPreGame)
		RegisterWithUpvalue(L, "PreGame", kPreGame, pPreGame);

	// IncludeFileList: every Lua file the VFS can reach (the winner per name), sorted by name.
	// ASSUMPTION (trace): the engine's list holds the full paths of every VFS Lua file.
	lua_getglobal(L, "IncludeFileList");
	if(lua_istable(L, -1))
	{
		std::vector<std::string> paths = kVfs.AllPaths();
		for(size_t i = 0; i < paths.size(); ++i)
		{
			lua_pushstring(L, paths[i].c_str());
			lua_rawseti(L, -2, (int)lua_objlen(L, -2) + 1);
		}
	}
	lua_pop(L, 1);

	return kScripts.RunString(L, kLuaPrelude, "=HHLuaGlobals") && kScripts.RunString(L, kUIContextLua, "=HHUIContext");
}

lua_State* HHLuaCreateUIContext(HHScriptSystem& kScripts, const std::string& strName, bool bHidden)
{
	lua_State* L = kScripts.CreateLuaThread(strName.c_str());
	if(!L)
		return NULL;
	// getfenv(1) in this chunk is the thread's environment.
	const std::string chunk = "__HHMakeUIContext(getfenv(1), [==[" + strName + "]==], " + (bHidden ? "true" : "false") + ")";
	if(!kScripts.RunString(L, chunk.c_str(), "=HHUIContext"))
	{
		kScripts.FreeLuaThread(L);
		return NULL;
	}
	return L;
}
