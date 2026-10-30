#include "engine/HHRuntime.h"

namespace
{
	struct HHLock
	{
		CRITICAL_SECTION cs;
		HHLock() { InitializeCriticalSection(&cs); }
		~HHLock() { DeleteCriticalSection(&cs); }
	};
	struct HHScoped
	{
		HHLock& l;
		explicit HHScoped(HHLock& lock) : l(lock) { EnterCriticalSection(&l.cs); }
		~HHScoped() { LeaveCriticalSection(&l.cs); }
	private:
		HHScoped& operator=(const HHScoped&);
	};

	HHLock s_logLock;
	HHLock s_callLock;
	FILE* s_pLog = NULL;
	LARGE_INTEGER s_qpcStart;
	LARGE_INTEGER s_qpcFreq;
	const char* s_szPhase = "startup";

	struct CallEntry
	{
		bool stub;
		long count;
		int order;
		std::string phase;
	};
	typedef std::map<std::string, CallEntry> CallMap;
	CallMap s_calls;
	int s_iNextOrder = 0;

	struct TimerInit
	{
		TimerInit() { QueryPerformanceFrequency(&s_qpcFreq); QueryPerformanceCounter(&s_qpcStart); }
	} s_timerInit;

	void WriteLogLine(const char* szPrefix, const std::string& strText, bool bError)
	{
		HHScoped lock(s_logLock);
		FILE* pConsole = bError ? stderr : stdout;
		fprintf(pConsole, "%s%s\n", szPrefix, strText.c_str());
		fflush(pConsole);
		if(s_pLog)
		{
			fprintf(s_pLog, "[%9.3f] %s%s\n", HHSeconds(), szPrefix, strText.c_str());
			fflush(s_pLog);
		}
	}

	void Hit(const char* szName, bool bStub)
	{
		bool bFirst = false;
		{
			HHScoped lock(s_callLock);
			CallMap::iterator it = s_calls.find(szName);
			if(it == s_calls.end())
			{
				CallEntry e;
				e.stub = bStub;
				e.count = 0;
				e.order = s_iNextOrder++;
				e.phase = s_szPhase;
				it = s_calls.insert(std::make_pair(std::string(szName), e)).first;
				bFirst = true;
			}
			it->second.count++;
		}
		if(bFirst)
		{
			if(bStub)
				HHLog("stub call: %s (phase %s)", szName, s_szPhase);
			else if(HHGetOptions().verbose)
				HHLog("engine call: %s (phase %s)", szName, s_szPhase);
		}
		if(bStub && HHGetOptions().strict)
			HHFatal("--strict: unimplemented engine method called: %s (phase %s)", szName, s_szPhase);
	}
}

HHOptions& HHGetOptions()
{
	static HHOptions s_options;
	return s_options;
}

HHPaths& HHGetPaths()
{
	static HHPaths s_paths;
	return s_paths;
}

double HHSeconds()
{
	LARGE_INTEGER now;
	QueryPerformanceCounter(&now);
	return double(now.QuadPart - s_qpcStart.QuadPart) / double(s_qpcFreq.QuadPart);
}

std::string HHFormatV(const char* szFormat, va_list args)
{
	char szBuffer[2048];
	int n = _vsnprintf_s(szBuffer, sizeof(szBuffer), _TRUNCATE, szFormat, args);
	if(n >= 0)
		return std::string(szBuffer, n);
	// Too long for the stack buffer: format into a heap buffer.
	std::vector<char> big(65536);
	n = _vsnprintf_s(&big[0], big.size(), _TRUNCATE, szFormat, args);
	return std::string(&big[0], n >= 0 ? n : strlen(&big[0]));
}

std::string HHFormat(const char* szFormat, ...)
{
	va_list args;
	va_start(args, szFormat);
	std::string s = HHFormatV(szFormat, args);
	va_end(args);
	return s;
}

void HHLog(const char* szFormat, ...)
{
	va_list args;
	va_start(args, szFormat);
	std::string s = HHFormatV(szFormat, args);
	va_end(args);
	WriteLogLine("", s, false);
}

void HHLogError(const char* szFormat, ...)
{
	va_list args;
	va_start(args, szFormat);
	std::string s = HHFormatV(szFormat, args);
	va_end(args);
	WriteLogLine("ERROR: ", s, true);
}

void HHFatal(const char* szFormat, ...)
{
	va_list args;
	va_start(args, szFormat);
	std::string s = HHFormatV(szFormat, args);
	va_end(args);
	WriteLogLine("FATAL: ", s, true);
	HHLogClose();
	TerminateProcess(GetCurrentProcess(), 3);
}

void HHLogOpen(const std::string& strLogsDir)
{
	HHScoped lock(s_logLock);
	if(s_pLog)
		return;
	std::wstring path = HHWiden(strLogsDir + "Headless.log");
	s_pLog = _wfsopen(path.c_str(), L"w", _SH_DENYWR);
}

void HHLogClose()
{
	HHScoped lock(s_logLock);
	if(s_pLog)
	{
		fclose(s_pLog);
		s_pLog = NULL;
	}
}

std::wstring HHWiden(const std::string& str, UINT uiCodePage)
{
	if(str.empty())
		return std::wstring();
	int n = MultiByteToWideChar(uiCodePage, 0, str.c_str(), (int)str.size(), NULL, 0);
	std::wstring w(n, L'\0');
	MultiByteToWideChar(uiCodePage, 0, str.c_str(), (int)str.size(), &w[0], n);
	return w;
}

std::string HHNarrow(const std::wstring& wstr, UINT uiCodePage)
{
	if(wstr.empty())
		return std::string();
	int n = WideCharToMultiByte(uiCodePage, 0, wstr.c_str(), (int)wstr.size(), NULL, 0, NULL, NULL);
	std::string s(n, '\0');
	WideCharToMultiByte(uiCodePage, 0, wstr.c_str(), (int)wstr.size(), &s[0], n, NULL, NULL);
	return s;
}

std::string HHLower(const std::string& str)
{
	std::string s(str);
	for(size_t i = 0; i < s.size(); ++i)
		if(s[i] >= 'A' && s[i] <= 'Z')
			s[i] = char(s[i] - 'A' + 'a');
	return s;
}

std::string HHFullPath(const std::string& strPath)
{
	std::wstring w = HHWiden(strPath);
	wchar_t szBuffer[4096];
	DWORD n = GetFullPathNameW(w.c_str(), 4096, szBuffer, NULL);
	if(n == 0 || n >= 4096)
		return strPath;
	return HHNarrow(std::wstring(szBuffer, n));
}

bool HHFileExists(const std::string& strPath)
{
	DWORD attr = GetFileAttributesW(HHWiden(strPath).c_str());
	return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

bool HHEnsureDirectory(const std::string& strPath)
{
	std::wstring w = HHWiden(strPath);
	while(!w.empty() && (w[w.size() - 1] == L'\\' || w[w.size() - 1] == L'/'))
		w.erase(w.size() - 1);
	if(w.empty())
		return true;
	DWORD attr = GetFileAttributesW(w.c_str());
	if(attr != INVALID_FILE_ATTRIBUTES)
		return (attr & FILE_ATTRIBUTE_DIRECTORY) != 0;
	size_t slash = w.find_last_of(L"\\/");
	if(slash != std::wstring::npos && slash > 0 && w[slash - 1] != L':')
	{
		if(!HHEnsureDirectory(HHNarrow(w.substr(0, slash))))
			return false;
	}
	return CreateDirectoryW(w.c_str(), NULL) != 0 || GetLastError() == ERROR_ALREADY_EXISTS;
}

void HHSetPhase(const char* szPhase)
{
	s_szPhase = szPhase;
	HHLog("-- phase: %s", szPhase);
}

const char* HHGetPhase()
{
	return s_szPhase;
}

void HHStubHit(const char* szName)
{
	Hit(szName, true);
}

long* HHImplCounter(const char* szName)
{
	Hit(szName, false);
	HHScoped lock(s_callLock);
	return &s_calls[szName].count;  // a map's entries stay where they are
}

std::vector<HHCallRecord> HHGetCallRecords()
{
	HHScoped lock(s_callLock);
	std::vector<HHCallRecord> out;
	for(CallMap::const_iterator it = s_calls.begin(); it != s_calls.end(); ++it)
	{
		HHCallRecord r;
		r.name = it->first;
		r.stub = it->second.stub;
		r.count = it->second.count;
		r.order = it->second.order;
		r.phase = it->second.phase;
		out.push_back(r);
	}
	return out;
}
