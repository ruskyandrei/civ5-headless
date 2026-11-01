// ICvEngineScriptSystem1 on Lua 5.1 (lua51_Win32.dll from the game folder).
//
// One main state holds the shared globals (_G): the standard libraries, GameInfo, DB,
// GameDefines, the DLL's script libraries, Threads and IncludeFileList (which the DLL's
// embedded VP_LUAAPI script reads). CreateLuaThread makes a coroutine with its own
// environment table {StateName = name} whose metatable falls back to _G, the way the
// engine's contexts see shared globals; Threads[thread] = env.
#pragma once
#include "HHCommon.h"
#include "gen/HHStubs.gen.h"

class HHScriptArgs : public HHStubScriptArgs
{
public:
	HHScriptArgs() {}
	virtual ~HHScriptArgs() {}

	unsigned int Push(bool bValue);
	unsigned int Push(const char* szValue, size_t lenValue);
	unsigned int Push(int iValue);
	unsigned int Push(float fValue);
	unsigned int PushNULL();
	unsigned int Count();
	ArgType GetType(unsigned int argIndex);
	bool GetBool(unsigned int argIndex, bool& bValue);
	bool GetFloat(unsigned int argIndex, float& fValue);
	bool GetInt(unsigned int argIndex, int& iValue);
	bool GetString(unsigned int argIndex, char*& szValue);

	// Pushes every argument onto a Lua stack; returns the number pushed.
	int PushAll(lua_State* L) const;
	// Empty again for the next hook, keeping its memory.
	void Clear();

private:
	// No string in here: the DLL pushes millions of numbers, so adding an Arg has to stay cheap.
	struct Arg
	{
		ArgType eType;
		union
		{
			bool b;
			int i;
			float f;
			unsigned int iString;  // index in m_strings
		};
	};
	unsigned int Add(const Arg& a);
	const Arg* At(unsigned int argIndex) const;
	std::vector<Arg> m_args;
	std::vector<std::string> m_strings;
};

// One Lua error, for the run report.
struct HHLuaErrorRecord
{
	std::string context;   // StateName of the thread, or the GameEvents/Events name for listener errors
	std::string file;      // chunk the error points at ("" when the message has no position)
	int line;
	std::string message;   // first line of the message, without the position
	std::string phase;     // host phase (HHGetPhase) at the error
	bool counted;          // counted in GetErrorCount (stub-context errors are not)
};

class HHScriptSystem : public HHStubScriptSystem
{
public:
	HHScriptSystem();
	~HHScriptSystem();

	lua_State* CreateLuaThread(_In_z_ const char* szName);
	void FreeLuaThread(_In_opt_ lua_State* L);
	bool LoadFile(_In_ lua_State* L, _In_z_ const char* szFilename);
	ICvEngineScriptSystemArgs1* CreateArgs();
	ICvEngineScriptSystemArgs1* CreateArgs(uint uiReserve);
	void DestroyArgs(_In_opt_ ICvEngineScriptSystemArgs1* pkArgs);
	bool CallCFunction(_In_ lua_State* L, lua_CFunction func, void* args);
	bool CallFunction(_In_ lua_State* L, unsigned int nArgs, unsigned int nRet);
	bool CallHook(_In_z_ const char* szName, _In_opt_ ICvEngineScriptSystemArgs1* args, bool& value);
	bool CallAccumulator(_In_z_ const char* szName, _In_opt_ ICvEngineScriptSystemArgs1* args, int& value);
	bool CallAccumulator(_In_z_ const char* szName, _In_opt_ ICvEngineScriptSystemArgs1* args, float& value);
	bool CallTestAll(_In_z_ const char* szName, _In_opt_ ICvEngineScriptSystemArgs1* args, bool& value);
	bool CallTestAny(_In_z_ const char* szName, _In_opt_ ICvEngineScriptSystemArgs1* args, bool& value);

	lua_State* GetMainState() const { return m_L; }
	// The engine's ipairs (measured, runs hh-m5-ipairs-probe/-probe2): ipairs(t) returns the stock iterator,
	// t and an initial control of -1 when t[0] ~= nil (else 0), so a loop visits t[0] first. Replaces the
	// global ipairs of L (every thread of a state inherits it through its environment's __index).
	static void InstallEngineIpairs(lua_State* L);
	// The game runs with EnableLuaDebugLibrary = 0: only the boot-time VP_LUAAPI thread reaches debug
	// (its chunk keeps debug.getinfo in a local). Removes debug from _G once that thread is done.
	void HideDebugLibrary();
	// The prelude calls this when a GameEvents listener list changes.
	void GameEventListenersChanged() { m_bListenedEventsStale = true; }
	// Runs the GameEvents listeners of szName (mode: hook, testall, testany, accum); returns handled.
	bool DispatchGameEvent(const char* szName, ICvEngineScriptSystemArgs1* pArgs, const char* szMode, double& dValue);
	// Closes the Lua state (runs every __gc, some of which are DLL code): call while the
	// game DLL, its game data and the databases are still alive.
	void Shutdown();
	// Resolves a script name against the search folders; empty if not found.
	std::string ResolveFile(const char* szFilename) const;
	// Runs a chunk of Lua in L with a traceback handler; logs and returns false on error.
	bool RunString(lua_State* L, const char* szChunk, const char* szChunkName);
	size_t GetThreadCount() const { return m_threads.size(); }
	// Errors that fail a run: every Lua error except those raised in stub UI contexts (add-ins) and in listeners.
	int GetErrorCount() const { return m_iErrors; }
	// Every error (the first kMaxErrorRecords in full) and the total.
	const std::vector<HHLuaErrorRecord>& GetErrorRecords() const { return m_errorRecords; }
	int GetErrorTotal() const { return m_iErrorTotal; }
	// While false, errors are recorded but not counted in GetErrorCount (stub UI contexts).
	void SetCountErrors(bool bCount) { m_bCountErrors = bCount; }
	void RecordError(const std::string& strContext, const char* szMessage, bool bCounted);
	static const size_t kMaxErrorRecords = 500;

	static FILogFile* GetLuaLog();

	// include() implementation (pushes the result table; returns 1).
	int Include(lua_State* L, const char* szName, bool bPattern);
	// Loads and runs a file in L's environment; logs errors.
	bool RunFile(lua_State* L, const std::string& strPath);
	static std::string StateName(lua_State* L);
	const std::vector<std::string>& GetLoadedFiles() const { return m_loadedFiles; }
	void LogIncludeSummary() const;
	// The DLL's script utility: its libraries are registered in every new thread.
	void SetScriptUtility(ICvScriptSystemUtility1* pUtility) { m_pUtility = pUtility; }

private:
	bool ProtectedCall(lua_State* L, int nArgs, int nRet, const char* szWhat);
	bool HasGameEventListeners(const char* szName);
	lua_State* m_L;
	std::map<lua_State*, int> m_threads; // thread -> registry reference
	void RecordInclude(const std::string& strPath);
	void Failed(lua_State* L, const char* szMessage);
	int m_iErrors;
	int m_iErrorTotal;
	bool m_bCountErrors;
	std::vector<HHLuaErrorRecord> m_errorRecords;
	ICvScriptSystemUtility1* m_pUtility;
	std::vector<std::string> m_loadedFiles;        // every file loaded, in order
	std::map<std::string, int> m_includeCounts;    // "context|name" -> include calls
	std::vector<HHScriptArgs*> m_freeArgs;         // DestroyArgs keeps them for CreateArgs
	std::vector<std::string> m_listenedEvents;     // the GameEvents that have a listener, usually few
	bool m_bListenedEventsStale;
};
