#include "lua/HHScriptSystem.h"
#include "engine/HHRuntime.h"
#include "engine/HHLogFiles.h"
#include "setup/HHVfs.h"

namespace
{
	// print(...) -> Lua.log, as "StateName: text".
	int HHLuaPrint(lua_State* L)
	{
		std::string line;
		const int n = lua_gettop(L);
		lua_getglobal(L, "tostring");
		for(int i = 1; i <= n; ++i)
		{
			lua_pushvalue(L, -1);
			lua_pushvalue(L, i);
			lua_call(L, 1, 1);
			const char* s = lua_tostring(L, -1);
			if(i > 1)
				line += '\t';
			line += s ? s : "?";
			lua_pop(L, 1);
		}
		lua_pop(L, 1);
		lua_getglobal(L, "StateName");
		const char* szState = lua_tostring(L, -1);
		std::string state = szState ? szState : "Main";
		lua_pop(L, 1);
		HHScriptSystem::GetLuaLog()->Msg("%s: %s", state.c_str(), line.c_str());
		return 0;
	}

	// Error handler: debug.traceback, kept in the registry so it works whether or not
	// scripts can see the debug library.
	int HHLuaTraceback(lua_State* L)
	{
		if(!lua_isstring(L, 1))
			return 1;
		lua_getfield(L, LUA_REGISTRYINDEX, "HHTraceback");
		if(!lua_isfunction(L, -1))
		{
			lua_pop(L, 1);
			return 1;
		}
		lua_pushvalue(L, 1);
		lua_pushinteger(L, 2);
		lua_call(L, 2, 1);
		return 1;
	}

	// __HHReportError(message, where): see HHScriptSystem::RecordError (not counted: listener errors).
	int HHLuaReportError(lua_State* L)
	{
		HHScriptSystem* pSystem = (HHScriptSystem*)lua_touserdata(L, lua_upvalueindex(1));
		const char* szMessage = lua_tostring(L, 1);
		const char* szWhere = lua_tostring(L, 2);
		pSystem->RecordError(szWhere ? szWhere : "Lua", szMessage, false);
		return 0;
	}

	// include(name[, bPattern]): see HHScriptSystem::Include.
	int HHLuaInclude(lua_State* L)
	{
		HHScriptSystem* pSystem = (HHScriptSystem*)lua_touserdata(L, lua_upvalueindex(1));
		const char* szName = luaL_checkstring(L, 1);
		const bool bPattern = lua_toboolean(L, 2) != 0;
		return pSystem->Include(L, szName, bPattern);
	}

	struct ThreadsSetArgs
	{
		int iThreadRef;
		bool bClear;
	};

	// Threads[thread] = env (or nil), through any __newindex the DLL's scripts installed.
	int HHLuaSetThreadEntry(lua_State* L)
	{
		ThreadsSetArgs* pArgs = (ThreadsSetArgs*)lua_touserdata(L, 1);
		// Stack: [1] args, [2] env (only when setting)
		lua_getglobal(L, "Threads");
		if(!lua_istable(L, -1))
			return 0;
		lua_rawgeti(L, LUA_REGISTRYINDEX, pArgs->iThreadRef);
		if(pArgs->bClear)
		{
			lua_pushnil(L);
			lua_rawset(L, -3);
		}
		else
		{
			// rawset, not settable: the DLL's VP_LUAAPI chunk puts a __newindex listener on
			// Threads that re-metatables each new environment for the real engine's UI
			// contexts (and drops later global assignments until 'UI' is set). Headless
			// threads keep their _G fallback. ASSUMPTION (trace): the real engine's map-script
			// and gameplay threads are not affected by that listener either.
			lua_pushvalue(L, 2);
			lua_rawset(L, -3);
		}
		return 0;
	}

	// The engine registers the DLL's script libraries in every new Lua thread (trace S1:
	// RegisterScriptLibraries nested in CreateLuaThread("VP_LUAAPI")).
	int HHLuaRegisterLibrariesInThread(lua_State* L)
	{
		ICvScriptSystemUtility1* pUtility = (ICvScriptSystemUtility1*)lua_touserdata(L, 1);
		lua_settop(L, 0);
		pUtility->RegisterScriptLibraries(L);
		return 0;
	}

	bool ReadWholeFile(const std::string& strPath, std::string& strOut)
	{
		HANDLE h = CreateFileW(HHWiden(strPath).c_str(), GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
		if(h == INVALID_HANDLE_VALUE)
			return false;
		LARGE_INTEGER size;
		GetFileSizeEx(h, &size);
		strOut.resize((size_t)size.QuadPart);
		DWORD n = 0;
		BOOL ok = strOut.empty() ? TRUE : ReadFile(h, &strOut[0], (DWORD)strOut.size(), &n, NULL);
		CloseHandle(h);
		return ok && n == strOut.size();
	}

	bool IsAbsolute(const std::string& s)
	{
		return (s.size() > 2 && s[1] == ':') || (s.size() > 1 && s[0] == '\\' && s[1] == '\\');
	}

	std::string BaseName(const std::string& s)
	{
		size_t slash = s.find_last_of("\\/");
		return slash == std::string::npos ? s : s.substr(slash + 1);
	}

	bool FindFileBelow(const std::wstring& wstrDir, const std::wstring& wstrName, std::wstring& wstrOut, int iDepth)
	{
		if(iDepth > 12)
			return false;
		std::wstring candidate = wstrDir + L"\\" + wstrName;
		DWORD attr = GetFileAttributesW(candidate.c_str());
		if(attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY))
		{
			wstrOut = candidate;
			return true;
		}
		WIN32_FIND_DATAW fd;
		HANDLE h = FindFirstFileW((wstrDir + L"\\*").c_str(), &fd);
		if(h == INVALID_HANDLE_VALUE)
			return false;
		std::vector<std::wstring> subdirs;
		do
		{
			if((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && wcscmp(fd.cFileName, L".") && wcscmp(fd.cFileName, L".."))
				subdirs.push_back(fd.cFileName);
		} while(FindNextFileW(h, &fd));
		FindClose(h);
		std::sort(subdirs.begin(), subdirs.end());
		for(size_t i = 0; i < subdirs.size(); ++i)
			if(FindFileBelow(wstrDir + L"\\" + subdirs[i], wstrName, wstrOut, iDepth + 1))
				return true;
		return false;
	}
}

//------------------------------------------------------------------------------
// HHScriptArgs
//------------------------------------------------------------------------------
// Assumption until the M0 trace: indices are 0-based and Push returns the new count.
unsigned int HHScriptArgs::Add(const Arg& a)
{
	// The engine returns the 0-based index of the pushed argument (contract G9).
	m_args.push_back(a);
	return (unsigned int)m_args.size() - 1;
}

const HHScriptArgs::Arg* HHScriptArgs::At(unsigned int argIndex) const
{
	return argIndex < m_args.size() ? &m_args[argIndex] : NULL;
}

unsigned int HHScriptArgs::Push(bool bValue)
{
	HH_IMPL_CALL("ICvEngineScriptSystemArgs1::Push");
	Arg a; a.eType = ARGTYPE_BOOL; a.b = bValue;
	return Add(a);
}

unsigned int HHScriptArgs::Push(const char* szValue, size_t lenValue)
{
	HH_IMPL_CALL("ICvEngineScriptSystemArgs1::Push");
	Arg a; a.eType = ARGTYPE_STRING; a.iString = (unsigned int)m_strings.size();
	m_strings.push_back(szValue ? std::string(szValue, lenValue) : std::string());
	return Add(a);
}

unsigned int HHScriptArgs::Push(int iValue)
{
	HH_IMPL_CALL("ICvEngineScriptSystemArgs1::Push");
	Arg a; a.eType = ARGTYPE_INT; a.i = iValue;
	return Add(a);
}

unsigned int HHScriptArgs::Push(float fValue)
{
	HH_IMPL_CALL("ICvEngineScriptSystemArgs1::Push");
	Arg a; a.eType = ARGTYPE_FLOAT; a.f = fValue;
	return Add(a);
}

unsigned int HHScriptArgs::PushNULL()
{
	HH_IMPL_CALL("ICvEngineScriptSystemArgs1::PushNULL");
	Arg a; a.eType = ARGTYPE_NULL; a.i = 0;
	return Add(a);
}

unsigned int HHScriptArgs::Count()
{
	HH_IMPL_CALL("ICvEngineScriptSystemArgs1::Count");
	return (unsigned int)m_args.size();
}

ICvEngineScriptSystemArgs1::ArgType HHScriptArgs::GetType(unsigned int argIndex)
{
	HH_IMPL_CALL("ICvEngineScriptSystemArgs1::GetType");
	const Arg* a = At(argIndex);
	return a ? a->eType : ARGTYPE_NONE;
}

bool HHScriptArgs::GetBool(unsigned int argIndex, bool& bValue)
{
	HH_IMPL_CALL("ICvEngineScriptSystemArgs1::GetBool");
	const Arg* a = At(argIndex);
	if(!a || a->eType != ARGTYPE_BOOL)
		return false;
	bValue = a->b;
	return true;
}

bool HHScriptArgs::GetFloat(unsigned int argIndex, float& fValue)
{
	HH_IMPL_CALL("ICvEngineScriptSystemArgs1::GetFloat");
	const Arg* a = At(argIndex);
	if(!a || a->eType != ARGTYPE_FLOAT)
		return false;
	fValue = a->f;
	return true;
}

bool HHScriptArgs::GetInt(unsigned int argIndex, int& iValue)
{
	HH_IMPL_CALL("ICvEngineScriptSystemArgs1::GetInt");
	const Arg* a = At(argIndex);
	if(!a || a->eType != ARGTYPE_INT)
		return false;
	iValue = a->i;
	return true;
}

bool HHScriptArgs::GetString(unsigned int argIndex, char*& szValue)
{
	HH_IMPL_CALL("ICvEngineScriptSystemArgs1::GetString");
	const Arg* a = At(argIndex);
	if(!a || a->eType != ARGTYPE_STRING)
		return false;
	szValue = const_cast<char*>(m_strings[a->iString].c_str());
	return true;
}

int HHScriptArgs::PushAll(lua_State* L) const
{
	for(size_t i = 0; i < m_args.size(); ++i)
	{
		const Arg& a = m_args[i];
		switch(a.eType)
		{
		case ARGTYPE_BOOL: lua_pushboolean(L, a.b ? 1 : 0); break;
		case ARGTYPE_STRING: lua_pushlstring(L, m_strings[a.iString].c_str(), m_strings[a.iString].size()); break;
		case ARGTYPE_INT: lua_pushinteger(L, a.i); break;
		case ARGTYPE_FLOAT: lua_pushnumber(L, a.f); break;
		default: lua_pushnil(L); break;
		}
	}
	return (int)m_args.size();
}

void HHScriptArgs::Clear()
{
	m_args.clear();
	m_strings.clear();
}

//------------------------------------------------------------------------------
// HHScriptSystem
//------------------------------------------------------------------------------
FILogFile* HHScriptSystem::GetLuaLog()
{
	return HHLogFileMgr::Get().GetLog("Lua.log", 0);
}

namespace
{
	int HHEngineIpairs(lua_State* L)
	{
		luaL_checktype(L, 1, LUA_TTABLE);
		lua_pushvalue(L, lua_upvalueindex(1)); // the stock ipairs iterator
		lua_pushvalue(L, 1);
		lua_rawgeti(L, 1, 0);
		const bool bHasZero = !lua_isnil(L, -1);
		lua_pop(L, 1);
		lua_pushinteger(L, bHasZero ? -1 : 0);
		return 3;
	}
}

void HHScriptSystem::InstallEngineIpairs(lua_State* L)
{
	// Take the stock iterator from the stock ipairs, then wrap it.
	lua_getglobal(L, "ipairs");
	lua_newtable(L);
	lua_call(L, 1, 1); // first result: the iterator
	lua_pushcclosure(L, HHEngineIpairs, 1);
	lua_setglobal(L, "ipairs");
}

void HHScriptSystem::HideDebugLibrary()
{
	if(!m_L || HHGetOptions().luaDebugLibrary == false)
		return;
	lua_pushnil(m_L);
	lua_setglobal(m_L, "debug");
	HHLog("Lua: debug library removed from _G (the boot-time VP_LUAAPI thread is done)");
}

HHScriptSystem::HHScriptSystem() : m_L(NULL), m_iErrors(0), m_iErrorTotal(0), m_bCountErrors(true), m_pUtility(NULL), m_bListenedEventsStale(true)
{
	m_L = luaL_newstate();
	luaL_openlibs(m_L);
	InstallEngineIpairs(m_L);

	// Keep debug.traceback for the host's error handler before scripts can touch debug.
	lua_getglobal(m_L, "debug");
	if(lua_istable(m_L, -1))
	{
		lua_getfield(m_L, -1, "traceback");
		lua_setfield(m_L, LUA_REGISTRYINDEX, "HHTraceback");
	}
	lua_pop(m_L, 1);
	// The game runs with EnableLuaDebugLibrary = 0: UI contexts have no debug table (probe
	// hh-probe-include-20261025a) and neither has the Map Script thread (probe 2,
	// hh-m5-cdrift-probe2: pcall(debug.getinfo) fails). The boot-time VP_LUAAPI thread does reach
	// it: its chunk reads debug.getinfo into a local and logs "ran without errors". So debug stays
	// in _G until boot's CacheGameDatabaseData is done (HideDebugLibrary), or is hidden from the
	// start with --no-lua-debug.
	if(!HHGetOptions().luaDebugLibrary)
	{
		lua_pushnil(m_L);
		lua_setglobal(m_L, "debug");
	}

	lua_pushcfunction(m_L, HHLuaPrint);
	lua_setglobal(m_L, "print");

	lua_newtable(m_L);
	lua_setglobal(m_L, "Threads");
	lua_newtable(m_L);
	lua_setglobal(m_L, "IncludeFileList");

	lua_pushlightuserdata(m_L, this);
	lua_pushcclosure(m_L, HHLuaInclude, 1);
	lua_setglobal(m_L, "include");

	// __HHReportError(message, where): errors the Lua side catches itself (listeners), for the run report.
	lua_pushlightuserdata(m_L, this);
	lua_pushcclosure(m_L, HHLuaReportError, 1);
	lua_setglobal(m_L, "__HHReportError");
}

void HHScriptSystem::Failed(lua_State* L, const char* szMessage)
{
	const std::string message = szMessage ? szMessage : "(no message)";
	RecordError(StateName(L), message.c_str(), m_bCountErrors);
	if(m_bCountErrors)
		++m_iErrors;
}

// Splits "<chunk>:<line>: <text>" (the chunk may be a path with a drive letter) and keeps the first line of the text.
void HHScriptSystem::RecordError(const std::string& strContext, const char* szMessage, bool bCounted)
{
	++m_iErrorTotal;
	if(m_errorRecords.size() >= kMaxErrorRecords)
		return;
	HHLuaErrorRecord r;
	r.context = strContext;
	r.line = -1;
	r.phase = HHGetPhase();
	r.counted = bCounted;
	std::string msg = szMessage ? szMessage : "(no message)";
	const size_t eol = msg.find('\n');
	if(eol != std::string::npos)
		msg.erase(eol);
	for(size_t i = 0; i < msg.size(); ++i)
	{
		if(msg[i] != ':' || i + 1 >= msg.size() || !isdigit((unsigned char)msg[i + 1]))
			continue;
		size_t j = i + 1;
		while(j < msg.size() && isdigit((unsigned char)msg[j]))
			++j;
		if(j < msg.size() && msg[j] == ':')
		{
			r.file = msg.substr(msg[0] == '@' ? 1 : 0, msg[0] == '@' ? i - 1 : i);
			r.line = atoi(msg.c_str() + i + 1);
			msg = msg.substr(j + 1);
			while(!msg.empty() && msg[0] == ' ')
				msg.erase(0, 1);
			break;
		}
	}
	r.message = msg;
	m_errorRecords.push_back(r);
}

HHScriptSystem::~HHScriptSystem()
{
	Shutdown();
	for(size_t i = 0; i < m_freeArgs.size(); ++i)
		delete m_freeArgs[i];
}

void HHScriptSystem::Shutdown()
{
	if(!m_L)
		return;
	if(!m_threads.empty())
		HHLog("Lua shutdown: %u thread(s) never freed", (unsigned)m_threads.size());
	m_threads.clear();
	lua_close(m_L);
	m_L = NULL;
}

lua_State* HHScriptSystem::CreateLuaThread(const char* szName)
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::CreateLuaThread");
	if(!m_L)
	{
		HHLogError("CreateLuaThread(%s) after Lua shutdown", szName ? szName : "");
		return NULL;
	}
	lua_State* T = lua_newthread(m_L);
	const int iRef = luaL_ref(m_L, LUA_REGISTRYINDEX);
	m_threads[T] = iRef;

	// env = setmetatable({StateName = name}, {__index = _G})
	lua_newtable(m_L);
	lua_pushstring(m_L, szName ? szName : "");
	lua_setfield(m_L, -2, "StateName");
	lua_newtable(m_L);
	lua_pushvalue(m_L, LUA_GLOBALSINDEX);
	lua_setfield(m_L, -2, "__index");
	lua_setmetatable(m_L, -2);

	// The thread's globals are the environment.
	lua_pushvalue(m_L, -1);
	lua_xmove(m_L, T, 1);
	lua_replace(T, LUA_GLOBALSINDEX);

	// Threads[T] = env
	ThreadsSetArgs args = { iRef, false };
	lua_pushcfunction(m_L, HHLuaSetThreadEntry);
	lua_pushlightuserdata(m_L, &args);
	lua_pushvalue(m_L, -3);
	if(lua_pcall(m_L, 2, 0, 0) != 0)
	{
		HHLogError("CreateLuaThread(%s): Threads registration failed: %s", szName, lua_tostring(m_L, -1));
		lua_pop(m_L, 1);
	}
	lua_pop(m_L, 1); // env

	if(m_pUtility && lua_cpcall(T, HHLuaRegisterLibrariesInThread, m_pUtility) != 0)
	{
		HHLogError("CreateLuaThread(%s): RegisterScriptLibraries failed: %s", szName, lua_tostring(T, -1));
		lua_pop(T, 1);
	}
	// The DLL builds GameInfoActions and GameInfoTypes again for every thread: 840 KB of the same tables for each of about 30 UI
	// contexts, and Lua's collector lets the heap grow to twice what is live. Once HHLuaRegisterGameData has built them on the main
	// state, every thread reads those. Nothing in the game's or VP's Lua writes to them.
	static const char* const kSharedTables[] = { "GameInfoActions", "GameInfoTypes" };
	for(int i = 0; i < 2; ++i)
	{
		lua_getglobal(m_L, kSharedTables[i]);
		if(lua_istable(m_L, -1))
		{
			lua_xmove(m_L, T, 1);
			lua_setglobal(T, kSharedTables[i]);
		}
		else
			lua_pop(m_L, 1);
	}
	return T;
}

void HHScriptSystem::FreeLuaThread(lua_State* L)
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::FreeLuaThread");
	if(!L)
		return;
	std::map<lua_State*, int>::iterator it = m_threads.find(L);
	if(it == m_threads.end())
	{
		HHLogError("FreeLuaThread: unknown Lua thread %p", L);
		return;
	}
	ThreadsSetArgs args = { it->second, true };
	if(lua_cpcall(m_L, HHLuaSetThreadEntry, &args) != 0)
	{
		HHLogError("FreeLuaThread: Threads cleanup failed: %s", lua_tostring(m_L, -1));
		lua_pop(m_L, 1);
	}
	lua_settop(L, 0);
	luaL_unref(m_L, LUA_REGISTRYINDEX, it->second);
	m_threads.erase(it);
}

std::string HHScriptSystem::ResolveFile(const char* szFilename) const
{
	if(!szFilename || !*szFilename)
		return std::string();
	std::string name(szFilename);
	for(size_t i = 0; i < name.size(); ++i)
		if(name[i] == '/')
			name[i] = '\\';
	HHVfs& kVfs = HHGetVfs();
	// 1. Absolute path (map scripts from mods: MapScripts.FileName), re-rooted to --mods-dir.
	if(IsAbsolute(name))
	{
		const std::string rerooted = kVfs.Reroot(name);
		if(HHFileExists(rerooted))
			return rerooted;
		return HHFileExists(name) ? name : std::string();
	}
	// 2. VFS name (mods > DLC > base), also for a relative game path: MapScripts.FileName
	// "Assets\Maps\Highlands.lua" loads the activated mods' VFS Highlands.lua in the real game
	// (CP/VP ship Highlands, InlandSea and Terra; the base scripts fail with VP's AssignStartingPlots).
	if(const std::vector<HHVfsEntry>* pFound = kVfs.Find(name))
		return pFound->front().path;
	// 3. Relative to the game folder (MapScripts.FileName "Assets\Maps\Continents.lua").
	const std::string& gameDir = HHGetOptions().gameDir;
	if(!gameDir.empty() && HHFileExists(gameDir + "\\" + name))
		return gameDir + "\\" + name;
	// 4. Search folders (boot without a VFS): folder + relative name, then a tree search.
	const std::vector<std::string>& folders = HHGetOptions().luaSearch;
	for(size_t i = 0; i < folders.size(); ++i)
	{
		std::string candidate = folders[i] + "\\" + name;
		if(HHFileExists(candidate))
			return candidate;
	}
	const std::wstring wstrBase = HHWiden(BaseName(name));
	for(size_t i = 0; i < folders.size(); ++i)
	{
		std::wstring found;
		if(FindFileBelow(HHWiden(folders[i]), wstrBase, found, 0))
			return HHNarrow(found);
	}
	return std::string();
}

std::string HHScriptSystem::StateName(lua_State* L)
{
	lua_getglobal(L, "StateName");
	const char* sz = lua_tostring(L, -1);
	std::string name = sz ? sz : "Main";
	lua_pop(L, 1);
	return name;
}

bool HHScriptSystem::RunFile(lua_State* L, const std::string& strPath)
{
	std::string text;
	if(!ReadWholeFile(strPath, text))
	{
		HHLogError("cannot read %s", strPath.c_str());
		return false;
	}
	const std::string chunk = "@" + strPath;
	if(luaL_loadbuffer(L, text.c_str(), text.size(), chunk.c_str()) != 0)
	{
		HHLogError("Lua syntax error: %s", lua_tostring(L, -1));
		GetLuaLog()->Msg("Syntax Error: %s", lua_tostring(L, -1));
		Failed(L, lua_tostring(L, -1));
		lua_pop(L, 1);
		return false;
	}
	return ProtectedCall(L, 0, 0, strPath.c_str());
}

// include(name[, bPattern]) as measured in the real game (probe hh-probe-include-20261025a):
// the name resolves through the VFS (".lua" optional, mods over DLC over base, the
// later-activated mod wins), exactly one file is loaded into the caller's environment, and
// the result is a table of the included paths ({} for an unknown name, no error).
// With bPattern the name is a Lua pattern matched against whole file names, as
// GameCalendarUtilities' include("GameCalendarUtilities_Overrides_%w+.lua", true) uses it.
// ASSUMPTION (trace): every include runs the file again (no once-per-context guard), and a
// runtime error inside the file is logged and does not propagate to the caller.
int HHScriptSystem::Include(lua_State* L, const char* szName, bool bPattern)
{
	HHVfs& kVfs = HHGetVfs();
	const std::string context = StateName(L);
	std::vector<std::string> chosen;
	if(bPattern)
	{
		const std::string pattern = std::string("^") + szName + "$";
		std::vector<std::string> all = kVfs.AllPaths();
		for(size_t i = 0; i < all.size(); ++i)
		{
			const std::string base = BaseName(all[i]);
			lua_getglobal(L, "string");
			lua_getfield(L, -1, "match");
			lua_pushstring(L, base.c_str());
			lua_pushstring(L, pattern.c_str());
			const bool bMatch = lua_pcall(L, 2, 1, 0) == 0 && !lua_isnil(L, -1);
			lua_pop(L, 2);
			if(bMatch)
				chosen.push_back(all[i]);
		}
		if(chosen.empty())
			kVfs.LogResolution("include-pattern", szName, context, std::string(), NULL);
		for(size_t i = 0; i < chosen.size(); ++i)
			kVfs.LogResolution("include-pattern", szName, context + HHFormat(" (match %u)", (unsigned)i), chosen[i], NULL);
	}
	else
	{
		const std::vector<HHVfsEntry>* pFound = kVfs.Find(szName);
		std::string path;
		if(pFound)
			path = pFound->front().path;
		else
			path = ResolveFile(szName); // boot without a VFS
		kVfs.LogResolution("include", szName, context, path, pFound);
		if(!path.empty())
			chosen.push_back(path);
	}
	++m_includeCounts[context + "|" + HHVfs::Key(szName)];
	lua_newtable(L);
	const int iResult = lua_gettop(L);
	int n = 0;
	for(size_t i = 0; i < chosen.size(); ++i)
	{
		RecordInclude(chosen[i]);
		RunFile(L, chosen[i]);
		lua_pushstring(L, chosen[i].c_str());
		lua_rawseti(L, iResult, ++n);
	}
	return 1;
}

void HHScriptSystem::RecordInclude(const std::string& strPath)
{
	m_loadedFiles.push_back(strPath);
}

void HHScriptSystem::LogIncludeSummary() const
{
	for(std::map<std::string, int>::const_iterator it = m_includeCounts.begin(); it != m_includeCounts.end(); ++it)
		if(it->second > 1)
			HHLog("include: %s requested %d times (each run again; ASSUMPTION)", it->first.c_str(), it->second);
}

bool HHScriptSystem::ProtectedCall(lua_State* L, int nArgs, int nRet, const char* szWhat)
{
	const int base = lua_gettop(L) - nArgs;
	lua_pushcfunction(L, HHLuaTraceback);
	lua_insert(L, base);
	const int rc = lua_pcall(L, nArgs, nRet, base);
	lua_remove(L, base);
	if(rc != 0)
	{
		const char* szError = lua_tostring(L, -1);
		HHLogError("Lua error in %s: %s", szWhat, szError ? szError : "(no message)");
		GetLuaLog()->Msg("Runtime Error: %s", szError ? szError : "(no message)");
		Failed(L, szError);
		lua_pop(L, 1);
		if(nRet != LUA_MULTRET)
			for(int i = 0; i < nRet; ++i)
				lua_pushnil(L);
		return false;
	}
	return true;
}

bool HHScriptSystem::LoadFile(lua_State* L, const char* szFilename)
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::LoadFile");
	const std::string request = szFilename ? szFilename : "";
	const std::string path = ResolveFile(szFilename);
	HHGetVfs().LogResolution("LoadFile", request, StateName(L), path, HHGetVfs().Find(request));
	if(path.empty())
	{
		HHLogError("LoadFile: %s not found", request.c_str());
		return false;
	}
	RecordInclude(path);
	return RunFile(L, path);
}

bool HHScriptSystem::RunString(lua_State* L, const char* szChunk, const char* szChunkName)
{
	if(luaL_loadbuffer(L, szChunk, strlen(szChunk), szChunkName) != 0)
	{
		HHLogError("Lua syntax error in %s: %s", szChunkName, lua_tostring(L, -1));
		Failed(L, lua_tostring(L, -1));
		lua_pop(L, 1);
		return false;
	}
	return ProtectedCall(L, 0, 0, szChunkName);
}

// The DLL makes an args object for every hook it raises, millions in a long game, whether anything listens or not.
// So they are used again.
ICvEngineScriptSystemArgs1* HHScriptSystem::CreateArgs()
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::CreateArgs");
	if(m_freeArgs.empty())
		return new HHScriptArgs();
	HHScriptArgs* pArgs = m_freeArgs.back();
	m_freeArgs.pop_back();
	return pArgs;
}

ICvEngineScriptSystemArgs1* HHScriptSystem::CreateArgs(uint)
{
	return CreateArgs();
}

void HHScriptSystem::DestroyArgs(ICvEngineScriptSystemArgs1* pkArgs)
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::DestroyArgs");
	if(!pkArgs)
		return;
	HHScriptArgs* pArgs = static_cast<HHScriptArgs*>(pkArgs);
	pArgs->Clear();
	m_freeArgs.push_back(pArgs);
}

bool HHScriptSystem::CallCFunction(lua_State* L, lua_CFunction func, void* args)
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::CallCFunction");
	if(lua_cpcall(L, func, args) != 0)
	{
		const char* szError = lua_tostring(L, -1);
		HHLogError("Lua error in CallCFunction: %s", szError ? szError : "(no message)");
		GetLuaLog()->Msg("Runtime Error: %s", szError ? szError : "(no message)");
		Failed(L, szError);
		lua_pop(L, 1);
		return false;
	}
	return true;
}

bool HHScriptSystem::CallFunction(lua_State* L, unsigned int nArgs, unsigned int nRet)
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::CallFunction");
	return ProtectedCall(L, (int)nArgs, (int)nRet, "CallFunction");
}

// Whether a GameEvent has a listener, without asking Lua: the DLL raises millions of them in a long game and nearly all
// have none. The names come from __HHGameEventListeners, read again after every change (GameEventListenersChanged).
bool HHScriptSystem::HasGameEventListeners(const char* szName)
{
	if(m_bListenedEventsStale)
	{
		m_bListenedEventsStale = false;
		m_listenedEvents.clear();
		const int top = lua_gettop(m_L);
		lua_getglobal(m_L, "__HHGameEventListeners");
		if(lua_istable(m_L, -1))
		{
			lua_pushnil(m_L);
			while(lua_next(m_L, -2))
			{
				if(lua_type(m_L, -2) == LUA_TSTRING && lua_istable(m_L, -1) && lua_objlen(m_L, -1) > 0)
					m_listenedEvents.push_back(lua_tostring(m_L, -2));
				lua_pop(m_L, 1);
			}
		}
		lua_settop(m_L, top);
	}
	for(size_t i = 0; i < m_listenedEvents.size(); ++i)
		if(strcmp(m_listenedEvents[i].c_str(), szName) == 0)
			return true;
	return false;
}

// GameEvents (contract 10.1): the listeners live in the Lua prelude's __HHGameEventListeners; the
// dispatch (__HHGameEventDispatch) runs every listener in registration order with the hook's
// arguments. Returns handled; dValue is the in/out value. A name without listeners returns not
// handled with the value unchanged.
bool HHScriptSystem::DispatchGameEvent(const char* szName, ICvEngineScriptSystemArgs1* pArgs, const char* szMode, double& dValue)
{
	lua_State* L = m_L;
	if(!L || !szName || !HasGameEventListeners(szName))
		return false;
	const int top = lua_gettop(L);
	// A thread of its own: the DLL can raise a hook while Lua code runs on the main state.
	lua_State* T = lua_newthread(L);
	lua_getglobal(T, "__HHGameEventDispatch");
	lua_pushstring(T, szName);
	lua_pushstring(T, szMode);
	lua_pushnumber(T, dValue);
	int nArgs = 0;
	if(pArgs)
		nArgs = static_cast<HHScriptArgs*>(pArgs)->PushAll(T);
	bool bHandled = false;
	if(lua_pcall(T, 3 + nArgs, 2, 0) == 0)
	{
		bHandled = lua_toboolean(T, -2) != 0;
		if(lua_isnumber(T, -1))
			dValue = lua_tonumber(T, -1);
		else
			dValue = lua_toboolean(T, -1) ? 1.0 : 0.0;
	}
	else
	{
		HHLogError("GameEvents.%s: %s", szName, lua_tostring(T, -1));
		RecordError(std::string("GameEvents.") + szName, lua_tostring(T, -1), m_bCountErrors);
		if(m_bCountErrors)
			++m_iErrors;
	}
	lua_settop(L, top);
	return bHandled;
}

// CallHook's value is always false (measured), whatever the listeners return.
bool HHScriptSystem::CallHook(const char* szName, ICvEngineScriptSystemArgs1* args, bool& value)
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::CallHook");
	double d = 0.0;
	const bool bHandled = DispatchGameEvent(szName, args, "hook", d);
	value = false;
	return bHandled;
}

bool HHScriptSystem::CallAccumulator(const char* szName, ICvEngineScriptSystemArgs1* args, int& value)
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::CallAccumulator");
	double d = value;
	const bool bHandled = DispatchGameEvent(szName, args, "accum", d);
	value = (int)d;
	return bHandled;
}

bool HHScriptSystem::CallAccumulator(const char* szName, ICvEngineScriptSystemArgs1* args, float& value)
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::CallAccumulator");
	double d = value;
	const bool bHandled = DispatchGameEvent(szName, args, "accum", d);
	value = (float)d;
	return bHandled;
}

bool HHScriptSystem::CallTestAll(const char* szName, ICvEngineScriptSystemArgs1* args, bool& value)
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::CallTestAll");
	double d = value ? 1.0 : 0.0;
	const bool bHandled = DispatchGameEvent(szName, args, "testall", d);
	value = d != 0.0;
	return bHandled;
}

bool HHScriptSystem::CallTestAny(const char* szName, ICvEngineScriptSystemArgs1* args, bool& value)
{
	HH_IMPL_CALL("ICvEngineScriptSystem1::CallTestAny");
	double d = value ? 1.0 : 0.0;
	const bool bHandled = DispatchGameEvent(szName, args, "testany", d);
	value = d != 0.0;
	return bHandled;
}
