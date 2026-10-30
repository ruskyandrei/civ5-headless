// Common includes for the headless host (civ5headless.exe).
//
// The host is built with the same VC9 compiler, /MD CRT and STL settings as the game DLL
// (_SECURE_SCL=0, _HAS_ITERATOR_DEBUGGING=0 come from the build script), and includes the
// same Firaxis interface headers, so every class that crosses the boundary has one layout.
#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>
#include <algorithm>
#include <limits>
#include <tchar.h>
#include <intrin.h>
#include <MMSystem.h>
#include <list>
#include <math.h>
#include <assert.h>

// Macros and typedefs the DLL's precompiled header (CvGameCoreDLLPCH.h) defines before the
// shared Firaxis headers; the enum and interface headers rely on them.
#define CLOSED_ENUM
#define OPEN_ENUM
#define FLAG_ENUM
#define ENUM_META_VALUE
#define NODISCARD
#define BUILTIN_UNREACHABLE() __assume(0)
#define BUILTIN_TRAP() do { __ud2(); __assume(0); } while(0)
#define BUILTIN_ASSUME(expr) __assume(expr)
#define ASSERT_DIALOGUE(msg) ((void)0)
#define UNUSED_VARIABLE(x) ((void)(x))
#define DEBUG_VARIABLE(x) UNUSED_VARIABLE(x)
#define DllExport __declspec(dllexport)
typedef unsigned char byte;
typedef unsigned int uint;
typedef wchar_t wchar;

extern "C" {
#include <lua.h>
#include <lualib.h>
#include <lauxlib.h>
}

// Firaxis interface headers, in the order the DLL's precompiled header uses.
#include "CvGameCoreDLLUtil_Win32Headers.h"
#include <FireWorks/FDefNew.h>
#include <FireWorks/FFireTypes.h>
#include <FireWorks/FAssert.h>
#include <FireWorks/FILogFile.h>
#include <FireWorks/FDataStream.h>
#include "CvGameDatabase.h"
#include "CvGameCoreDLLUtil.h"
#include "ICvDLLUtility.h"
#include "ICvDLLUserInterface.h"
#include "ICvDLLScriptSystem.h"
#include "ICvDLLDatabaseUtility.h"
#include "CvDllInterfaces.h"
#include "CvLocalization.h"
#include "LuaExposures.h"

// Stub support for the generated bases in gen/HHStubs.gen.h.
void HHStubHit(const char* szName);
long* HHImplCounter(const char* szName);
#define HH_STUB_CALL(name) HHStubHit(name)
// One counter pointer per call site: the first call registers the method, the others only count (the engine calls come
// by the hundred thousand).
#define HH_IMPL_CALL(name) do { static long* s_pHHCount = NULL; if(s_pHHCount) ++*s_pHHCount; else s_pHHCount = HHImplCounter(name); } while(0)
template<class T> T HHStubDefault() { return T(); }

// Host log (Logs/Headless.log and stdout).
void HHLog(const char* szFormat, ...);
void HHLogError(const char* szFormat, ...);
