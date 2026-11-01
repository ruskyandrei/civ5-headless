#include "setup/HHDatabase.h"
#include "engine/HHRuntime.h"
#include "engine/HHLogFiles.h"
#include <psapi.h>

namespace
{
	// Database and localization messages go to the run's Logs, as the engine's Database.log.
	class HHDatabaseLogger : public Database::IDatabaseLogger
	{
	public:
		virtual void Log(const char* szMessage, LogTypes eType)
		{
			FILogFile* pLog = HHLogFileMgr::Get().GetLog("Database.log", 0);
			const char* szPrefix = eType == TYPE_ERROR ? "ERROR: " : (eType == TYPE_WARNING ? "WARNING: " : "");
			pLog->Msg("%s%s", szPrefix, szMessage ? szMessage : "");
			if(eType == TYPE_ERROR)
				HHLog("Database error: %s", szMessage ? szMessage : "");
		}
	};

	class HHLocalizationLogger : public Localization::ILocalizationLogger
	{
	public:
		virtual void Log(const char* szMessage, LogTypes eType)
		{
			FILogFile* pLog = HHLogFileMgr::Get().GetLog("Localization.log", 0);
			const char* szPrefix = eType == TYPE_ERROR ? "ERROR: " : (eType == TYPE_WARNING ? "WARNING: " : "");
			pLog->Msg("%s%s", szPrefix, szMessage ? szMessage : "");
		}
	};

	// The localization DLL reads the ICU data file into a block of its own, 9.6 MB in every process of which ICU touches 0.2 MB. Through
	// its import table it gets a read-only view of the file instead, which all the processes share: the block it allocates with the
	// file's size is the view, and the read into it does nothing. Anything else goes straight through.
	typedef HANDLE (WINAPI *PFNCreateFileW)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
	typedef BOOL (WINAPI *PFNReadFile)(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
	typedef void* (__cdecl *PFNAlloc)(size_t);
	typedef void (__cdecl *PFNFree)(void*);
	PFNCreateFileW s_pfnCreateFileW = NULL;
	PFNReadFile s_pfnReadFile = NULL;
	PFNAlloc s_pfnMalloc = NULL, s_pfnNew = NULL;
	PFNFree s_pfnFree = NULL, s_pfnDelete = NULL, s_pfnDeleteArr = NULL;
	std::wstring s_wstrIcuPath;
	HANDLE s_hIcuFile = INVALID_HANDLE_VALUE;
	void* s_pIcuView = NULL;
	size_t s_nIcuBytes = 0;
	bool s_bIcuViewGiven = false;

	HANDLE WINAPI IcuCreateFileW(LPCWSTR wsz, DWORD a1, DWORD a2, LPSECURITY_ATTRIBUTES a3, DWORD a4, DWORD a5, HANDLE a6)
	{
		const HANDLE h = s_pfnCreateFileW(wsz, a1, a2, a3, a4, a5, a6);
		if(h != INVALID_HANDLE_VALUE && !s_pIcuView && wsz && _wcsicmp(wsz, s_wstrIcuPath.c_str()) == 0)
		{
			LARGE_INTEGER size;
			if(GetFileSizeEx(h, &size) && size.HighPart == 0)
				if(HANDLE hMap = CreateFileMappingW(h, NULL, PAGE_READONLY, 0, 0, NULL))
				{
					s_pIcuView = MapViewOfFile(hMap, FILE_MAP_READ, 0, 0, 0);
					CloseHandle(hMap);
					s_nIcuBytes = size.LowPart;
					s_hIcuFile = h;
				}
		}
		return h;
	}
	BOOL WINAPI IcuReadFile(HANDLE h, LPVOID pv, DWORD n, LPDWORD pnRead, LPOVERLAPPED pOverlapped)
	{
		if(h == s_hIcuFile && pv == s_pIcuView && n == s_nIcuBytes && pnRead && !pOverlapped)
		{
			*pnRead = n;
			return TRUE;
		}
		return s_pfnReadFile(h, pv, n, pnRead, pOverlapped);
	}
	void* IcuViewFor(size_t n)
	{
		if(!s_pIcuView || s_bIcuViewGiven || n != s_nIcuBytes)
			return NULL;
		s_bIcuViewGiven = true;
		return s_pIcuView;
	}
	void* __cdecl IcuMalloc(size_t n) { void* p = IcuViewFor(n); return p ? p : s_pfnMalloc(n); }
	void* __cdecl IcuNew(size_t n) { void* p = IcuViewFor(n); return p ? p : s_pfnNew(n); }
	bool IcuRelease(void* p)
	{
		if(!p || p != s_pIcuView)
			return false;
		UnmapViewOfFile(p);
		s_pIcuView = NULL;
		return true;
	}
	void __cdecl IcuFree(void* p) { if(!IcuRelease(p)) s_pfnFree(p); }
	void __cdecl IcuDelete(void* p) { if(!IcuRelease(p)) s_pfnDelete(p); }
	void __cdecl IcuDeleteArr(void* p) { if(!IcuRelease(p)) s_pfnDeleteArr(p); }

	template<class T> void PatchImport(HMODULE h, const char* szDll, const char* szName, T pfnNew, T& pfnOld)
	{
		BYTE* pBase = (BYTE*)h;
		const IMAGE_NT_HEADERS* pNt = (const IMAGE_NT_HEADERS*)(pBase + ((const IMAGE_DOS_HEADER*)pBase)->e_lfanew);
		const IMAGE_DATA_DIRECTORY& dir = pNt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
		if(!dir.VirtualAddress)
			return;
		for(const IMAGE_IMPORT_DESCRIPTOR* pImp = (const IMAGE_IMPORT_DESCRIPTOR*)(pBase + dir.VirtualAddress); pImp->Name; ++pImp)
		{
			if(_stricmp((const char*)(pBase + pImp->Name), szDll) != 0 || !pImp->OriginalFirstThunk)
				continue;
			const IMAGE_THUNK_DATA* pName = (const IMAGE_THUNK_DATA*)(pBase + pImp->OriginalFirstThunk);
			IMAGE_THUNK_DATA* pAddr = (IMAGE_THUNK_DATA*)(pBase + pImp->FirstThunk);
			for(; pName->u1.AddressOfData; ++pName, ++pAddr)
			{
				if(IMAGE_SNAP_BY_ORDINAL(pName->u1.Ordinal) || strcmp((const char*)((const IMAGE_IMPORT_BY_NAME*)(pBase + pName->u1.AddressOfData))->Name, szName) != 0)
					continue;
				DWORD dwOld = 0;
				if(VirtualProtect(&pAddr->u1.Function, sizeof(pAddr->u1.Function), PAGE_READWRITE, &dwOld))
				{
					pfnOld = (T)pAddr->u1.Function;
					pAddr->u1.Function = (DWORD_PTR)pfnNew;
					VirtualProtect(&pAddr->u1.Function, sizeof(pAddr->u1.Function), dwOld, &dwOld);
				}
				return;
			}
		}
	}

	void MapIcuData(const std::string& strIcuData)
	{
		const HMODULE h = GetModuleHandleW(L"CvLocalizationWin32Final Release.dll");
		if(!h)
			return;
		s_wstrIcuPath = HHWiden(strIcuData);
		PatchImport<PFNCreateFileW>(h, "KERNEL32.dll", "CreateFileW", IcuCreateFileW, s_pfnCreateFileW);
		PatchImport<PFNReadFile>(h, "KERNEL32.dll", "ReadFile", IcuReadFile, s_pfnReadFile);
		PatchImport<PFNAlloc>(h, "MSVCR90.dll", "malloc", IcuMalloc, s_pfnMalloc);
		PatchImport<PFNAlloc>(h, "MSVCR90.dll", "??2@YAPAXI@Z", IcuNew, s_pfnNew);
		PatchImport<PFNFree>(h, "MSVCR90.dll", "free", IcuFree, s_pfnFree);
		PatchImport<PFNFree>(h, "MSVCR90.dll", "??3@YAXPAX@Z", IcuDelete, s_pfnDelete);
		PatchImport<PFNFree>(h, "MSVCR90.dll", "??_V@YAXPAX@Z", IcuDeleteArr, s_pfnDeleteArr);
	}

	// Database::Connection owns its logger (it deletes it when the connection is destroyed), so every
	// connection gets its own heap logger. A shared static one was deleted at HHDatabases::Close: heap
	// corruption at shutdown that crashed or hung some builds after "game DLL unloaded".
	// Localization::SetLogger's ownership is unknown: a heap object as well, never deleted by the host.

	void Pragma(Database::Connection& kDb, const char* szPragma)
	{
		Database::Results kResults;
		if(!kDb.Execute(kResults, szPragma) || !kResults.Step())
			HHLog("%s: %s", szPragma, kDb.ErrorMessage());
	}

	// A copy in the run folder is this run's alone, so it needs no file locks: without this every statement checks for a hot
	// journal, locks and unlocks. And its pages come through a mapping, not one ReadFile per 1 KB page. What a query answers
	// depends on neither. 128 MB is more than the databases have; it only caps the mapping. --no-exclusive-db keeps the locks,
	// because once the DLL has written to it, exclusive mode shuts every other program out of the game database until the run ends.
	void ThisRunsAlone(Database::Connection& kDb)
	{
		if(!HHGetOptions().noExclusiveDb)
			Pragma(kDb, "PRAGMA locking_mode=EXCLUSIVE");
		Pragma(kDb, "PRAGMA mmap_size=134217728");
	}

	// This SQLite keeps its mapping until the connection closes, and the localization database stays open with its 50 MB read
	// once (the game asks the dictionary). VirtualUnlock on pages that are not locked takes them out of the working set, so the
	// peak working set a run reports stays what it was. The address range stays taken.
	void TrimMapping(const std::string& strFile)
	{
		const std::wstring wstrName = HHWiden(strFile.substr(strFile.find_last_of("\\/") + 1));
		MEMORY_BASIC_INFORMATION kInfo;
		for(BYTE* p = NULL; VirtualQuery(p, &kInfo, sizeof(kInfo)); p = (BYTE*)kInfo.BaseAddress + kInfo.RegionSize)
		{
			wchar_t szMapped[MAX_PATH];
			if(kInfo.Type != MEM_MAPPED || !GetMappedFileNameW(GetCurrentProcess(), kInfo.BaseAddress, szMapped, MAX_PATH))
				continue;
			const size_t nMapped = wcslen(szMapped);
			if(nMapped >= wstrName.size() && _wcsicmp(szMapped + nMapped - wstrName.size(), wstrName.c_str()) == 0)
				VirtualUnlock(kInfo.BaseAddress, kInfo.RegionSize);
		}
	}
}

HHDatabases::HHDatabases() : m_pGame(NULL), m_pLoc(NULL), m_bLocalization(false) {}

HHDatabases::~HHDatabases()
{
	ShutdownLocalization();
	Close();
}

bool HHDatabases::CopyIntoRun(const std::string& strSource, std::string& strCopy)
{
	const std::string& dbDir = HHGetPaths().db;
	size_t slash = strSource.find_last_of("\\/");
	std::string name = slash == std::string::npos ? strSource : strSource.substr(slash + 1);
	strCopy = dbDir + name;
	// Never open the source: it can be the live cache of a running game.
	if(!CopyFileW(HHWiden(strSource).c_str(), HHWiden(strCopy).c_str(), FALSE))
	{
		HHLogError("cannot copy %s to %s (error %lu)", strSource.c_str(), strCopy.c_str(), GetLastError());
		return false;
	}
	// A copy taken while SQLite holds a journal is still consistent for reading only if the
	// journal is absent; report it so a bad copy is recognisable.
	if(HHFileExists(strSource + "-journal") || HHFileExists(strSource + "-wal"))
		HHLog("warning: %s has a journal/WAL next to it; the copy may be mid-transaction", strSource.c_str());
	HHLog("copied %s -> %s", strSource.c_str(), strCopy.c_str());
	return true;
}

bool HHDatabases::OpenGameDatabase(const std::string& strSource)
{
	if(!CopyIntoRun(strSource, m_strGameCopy))
		return false;
	m_pGame = new Database::Connection();
	m_pGame->SetLogger(new HHDatabaseLogger());
	// The DLL writes to the game database (post-processing, ANALYZE): read-write on the copy.
	// Host strings are UTF-8, as SQLite expects for file names.
	if(!m_pGame->Open(m_strGameCopy.c_str(), Database::OPEN_READWRITE))
	{
		HHLogError("cannot open game database %s: %s", m_strGameCopy.c_str(), m_pGame->ErrorMessage());
		delete m_pGame;
		m_pGame = NULL;
		return false;
	}
	ThisRunsAlone(*m_pGame);
	return true;
}

bool HHDatabases::InitLocalization(const std::string& strLocSource, const std::string& strIcuData, const std::string& strLanguage)
{
	if(!CopyIntoRun(strLocSource, m_strLocCopy))
		return false;
	m_pLoc = new Database::Connection();
	m_pLoc->SetLogger(new HHDatabaseLogger());
	if(!m_pLoc->Open(m_strLocCopy.c_str(), Database::OPEN_READWRITE))
	{
		HHLogError("cannot open localization database %s: %s", m_strLocCopy.c_str(), m_pLoc->ErrorMessage());
		delete m_pLoc;
		m_pLoc = NULL;
		return false;
	}
	ThisRunsAlone(*m_pLoc);
	Localization::SetLogger(new HHLocalizationLogger());
	MapIcuData(strIcuData);
	const std::wstring icu = HHWiden(strIcuData);
	if(!Localization::Initialize(icu.c_str(), *m_pLoc, "Languages", strLanguage.c_str(), false))
	{
		HHLogError("Localization::Initialize failed (ICU %s, language %s)", strIcuData.c_str(), strLanguage.c_str());
		return false;
	}
	TrimMapping(m_strLocCopy);
	m_bLocalization = true;
	return true;
}

void HHDatabases::ShutdownLocalization()
{
	if(m_bLocalization)
	{
		Localization::Shutdown();
		m_bLocalization = false;
	}
}

void HHDatabases::Close()
{
	if(m_pLoc)
	{
		m_pLoc->Close();
		delete m_pLoc;
		m_pLoc = NULL;
	}
	if(m_pGame)
	{
		m_pGame->Close();
		delete m_pGame;
		m_pGame = NULL;
	}
}

int HHDatabases::CountRows(const char* szTable)
{
	if(!m_pGame)
		return -1;
	Database::Results kResults;
	std::string sql = HHFormat("SELECT COUNT(*) FROM \"%s\"", szTable);
	if(!m_pGame->Execute(kResults, sql.c_str()) || !kResults.Step())
		return -1;
	return kResults.GetInt(0);
}

int HHDatabases::IdRange(const char* szTable)
{
	if(!m_pGame)
		return -1;
	Database::Results kResults;
	std::string sql = HHFormat("SELECT MAX(ID) + 1 FROM \"%s\"", szTable);
	if(!m_pGame->Execute(kResults, sql.c_str()) || !kResults.Step())
		return -1;
	return kResults.GetInt(0);
}
