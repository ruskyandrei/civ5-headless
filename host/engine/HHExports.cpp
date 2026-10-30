// The 22 exe exports that the FireWorks code linked into the game DLL resolves with
// GetModuleHandle(NULL) + GetProcAddress (names in civ5headless.def).
//
// Findings from FirePlace/lib/FireWorksWin32.lib (disassembly) and the SDK's
// CivilizationV_Server.exe:
// - FMemHooks (operator new/delete, FireMalloc/FireFree) binds all FSBA* procs plus
//   GetSmallObjectAllocator once; it then calls GetSmallObjectAllocator(false) and uses the
//   small-block allocator only if that returns non-NULL. Every allocation then calls
//   FSBAAllocate (NULL means "use malloc") and every free calls FSBADeallocate (false means
//   "use free"). By default the host's allocator is "disabled": a non-NULL dummy instance so the
//   binding completes once, FSBAAllocate always NULL, FSBADeallocate always false, so all DLL
//   memory comes from the shared MSVCR90 heap. --sba makes it a real one (below).
// - GetTempHeap returns a TempHeap whose members the DLL's own (inlined/linked) TempHeap
//   code reads directly: lock at +0x00 (16-byte aligned), m_pMemHead +0x10,
//   m_uiCurrentOffset +0x14, m_uiCapacity +0x18, m_dwFlags +0x1c. Allocate bumps the offset
//   and returns NULL when full (callers then fall back to the heap); the last DeAllocate
//   resets the offset. The host provides a real 8 MB block.
// - FStringA/W_GetStringInitData return the shared empty-string header (all zero, then the
//   terminator); the server's are zero-initialised statics too.
// - GetLogFileMgr / PeekLogFileMgr return the FILogFileMgr; GetFileSystemMgr the
//   FIFileSystem. GetMemTrackerForDLL may return NULL (FireWorks then has no tracker).
// - The DLL contains no reference to FJobSet*, GetMemTrackerForDLL or
//   GetSmallObjectAllocatorInitParams, and exports std::_Init_locks::operator= itself; those
//   five are logging stubs here, with the server's FJobSetInitImpl behaviour (*p = 0).
#include "HHCommon.h"
#include "engine/HHRuntime.h"
#include "engine/HHLogFiles.h"
#include "engine/HHFileSystem.h"
#include <stddef.h>

namespace
{
	enum ExportId
	{
		EX_FJobSetAddJobImpl,
		EX_FJobSetInitImpl,
		EX_FJobSetUninitImpl,
		EX_FJobSetWaitImpl,
		EX_FSBAAllocate,
		EX_FSBADeallocate,
		EX_FSBADisable,
		EX_FSBAGetMaxAllocatorSize,
		EX_FSBAHasAllocatorForSize,
		EX_FSBAIsDisabled,
		EX_FSBAIsSmallBlock,
		EX_FSBAPreventCreation,
		EX_FStringA_GetStringInitData,
		EX_FStringW_GetStringInitData,
		EX_GetFileSystemMgr,
		EX_GetLogFileMgr,
		EX_GetMemTrackerForDLL,
		EX_GetSmallObjectAllocator,
		EX_GetSmallObjectAllocatorInitParams,
		EX_GetTempHeap,
		EX_PeekLogFileMgr,
		EX_InitLocksAssign,
		NUM_EXPORTS
	};

	const char* s_aszExportNames[NUM_EXPORTS] =
	{
		"FJobSetAddJobImpl", "FJobSetInitImpl", "FJobSetUninitImpl", "FJobSetWaitImpl",
		"FSBAAllocate", "FSBADeallocate", "FSBADisable", "FSBAGetMaxAllocatorSize",
		"FSBAHasAllocatorForSize", "FSBAIsDisabled", "FSBAIsSmallBlock", "FSBAPreventCreation",
		"FStringA_GetStringInitData", "FStringW_GetStringInitData", "GetFileSystemMgr",
		"GetLogFileMgr", "GetMemTrackerForDLL", "GetSmallObjectAllocator",
		"GetSmallObjectAllocatorInitParams", "GetTempHeap", "PeekLogFileMgr",
		"??4_Init_locks@std@@QAEAAV01@ABV01@@Z",
	};

	LONG s_alExportCounts[NUM_EXPORTS];
	const char* s_aszExportPhase[NUM_EXPORTS];

	inline void ExportHit(ExportId eId)
	{
		if(++s_alExportCounts[eId] == 1)  // the host has one thread, and a lost count would only be a count
		{
			s_aszExportPhase[eId] = HHGetPhase();
			HHLog("export first call: %s (phase %s)", s_aszExportNames[eId], HHGetPhase());
		}
	}

	// Unused exports: count and, under --strict, abort.
	void UnusedExportHit(ExportId eId)
	{
		ExportHit(eId);
		if(HHGetOptions().strict)
			HHFatal("--strict: unexpected call of export %s", s_aszExportNames[eId]);
	}

	// TempHeap layout as read by FireWorks' TempHeap code in the DLL.
	struct HHTempHeap
	{
		__declspec(align(16)) volatile LONG lLock;
		LONG alPad[3];
		BYTE* pMemHead;
		volatile LONG lCurrentOffset;
		UINT uiCapacity;
		DWORD dwFlags;
	};
	C_ASSERT(offsetof(HHTempHeap, pMemHead) == 0x10);
	C_ASSERT(offsetof(HHTempHeap, lCurrentOffset) == 0x14);
	C_ASSERT(offsetof(HHTempHeap, uiCapacity) == 0x18);
	C_ASSERT(offsetof(HHTempHeap, dwFlags) == 0x1c);

	const UINT kTempHeapBytes = 8u * 1024u * 1024u;
	HHTempHeap s_tempHeap;

	// FStringAData / FStringWData: alloc length, data length, locked, fixed, then the text.
	struct HHStringAInit { int iAllocLength; int iDataLength; USHORT bLocked; USHORT bFixed; char sz[4]; };
	struct HHStringWInit { int iAllocLength; int iDataLength; USHORT bLocked; USHORT bFixed; wchar_t wsz[2]; };
	HHStringAInit s_stringAInit;
	HHStringWInit s_stringWInit;

	// Dummy small-object allocator: only its address matters (non-NULL completes binding).
	struct HHSmallObjectAllocator { char ach[256]; } s_smallObjectAllocator;
	struct HHSmallObjectAllocatorInitParams { char ach[64]; } s_smallObjectAllocatorInitParams;
	volatile LONG s_lSBADisabled = 1;

	// --heap-perturb SEED (address-perturbation self-test): every DLL allocation passes FSBAAllocate
	// before FireWorks falls back to malloc, so spacer blocks of random sizes are allocated and freed
	// there, in the same MSVCR90 heap. The DLL's blocks then land at different addresses and in a
	// different relative order; game results must not change. Own LCG, never the game's RNGs.
	const int kPerturbSlots = 4096;
	void* s_apPerturb[kPerturbSlots];
	unsigned int s_uPerturbState = 0;
	bool s_bPerturb = false;
	CRITICAL_SECTION s_csPerturb;
	unsigned int PerturbNext()
	{
		s_uPerturbState = s_uPerturbState * 1103515245u + 12345u;
		return s_uPerturbState >> 8;
	}
	size_t PerturbSize()
	{
		unsigned int r = PerturbNext();
		unsigned int k = r & 255;
		r >>= 8;
		if(k < 200) return 4 + r % 252;
		if(k < 250) return 256 + r % 4096;
		return 4096 + r % 65536;
	}
	void PerturbSlot()
	{
		unsigned int i = PerturbNext() % kPerturbSlots;
		if(s_apPerturb[i])
			free(s_apPerturb[i]);
		s_apPerturb[i] = NULL;
		if(PerturbNext() & 1)
		{
			// Poison the spacer: a DLL block that reuses this memory later sees non-zero garbage
			// in members it fails to initialise (the CRT heap does not clear freed memory).
			const size_t n = PerturbSize();
			s_apPerturb[i] = malloc(n);
			if(s_apPerturb[i])
				memset(s_apPerturb[i], 0x5A + (int)(s_uPerturbState & 0x0F), n);
		}
	}
	void PerturbStep()
	{
		EnterCriticalSection(&s_csPerturb);
		if((PerturbNext() & 3) == 0)
			PerturbSlot();
		LeaveCriticalSection(&s_csPerturb);
	}
	volatile LONG s_lSBAPreventCreation = 0;

	// --sba: a real small-block allocator, as the real exe has one: size classes in 16-byte steps up to 512 bytes, 64 KB
	// pages out of one reserved range, LIFO free lists. A page belongs to one size class for good. When the range is used
	// up, or could not be reserved, FSBAAllocate answers NULL and the blocks come from the CRT heap, as without --sba.
	const size_t kSbaMax = 512, kSbaStep = 16, kSbaPage = 65536, kSbaReserve = 640u * 1024 * 1024;
	struct SbaNode { SbaNode* pNext; };
	SbaNode* s_apSbaFree[kSbaMax / kSbaStep + 1];
	BYTE* s_apSbaCur[kSbaMax / kSbaStep + 1];
	BYTE* s_apSbaEnd[kSbaMax / kSbaStep + 1];
	BYTE s_aSbaClass[kSbaReserve / kSbaPage];
	BYTE* s_pSbaBase = NULL;
	BYTE* s_pSbaNext = NULL;
	volatile LONG s_lSbaLock = 0;
	inline void SbaLock() { while(InterlockedExchange(&s_lSbaLock, 1)) YieldProcessor(); }
	inline void SbaUnlock() { s_lSbaLock = 0; }
	inline bool SbaOwns(void* p) { return s_pSbaBase && (BYTE*)p >= s_pSbaBase && (BYTE*)p < s_pSbaBase + kSbaReserve; }
	void* SbaAllocate(size_t nSize)
	{
		if(nSize > kSbaMax)
			return NULL;
		const size_t k = nSize ? (nSize + kSbaStep - 1) / kSbaStep : 1;
		void* p = NULL;
		SbaLock();
		if(SbaNode* pNode = s_apSbaFree[k])
		{
			s_apSbaFree[k] = pNode->pNext;
			p = pNode;
		}
		else
		{
			if(s_apSbaCur[k] == s_apSbaEnd[k] && s_pSbaNext < s_pSbaBase + kSbaReserve &&
			   VirtualAlloc(s_pSbaNext, kSbaPage, MEM_COMMIT, PAGE_READWRITE))
			{
				s_aSbaClass[(s_pSbaNext - s_pSbaBase) / kSbaPage] = (BYTE)k;
				s_apSbaCur[k] = s_pSbaNext;
				s_apSbaEnd[k] = s_pSbaNext + kSbaPage / (k * kSbaStep) * (k * kSbaStep);
				s_pSbaNext += kSbaPage;
			}
			if(s_apSbaCur[k] != s_apSbaEnd[k])
			{
				p = s_apSbaCur[k];
				s_apSbaCur[k] += k * kSbaStep;
			}
		}
		SbaUnlock();
		return p;
	}
	bool SbaFree(void* p)
	{
		if(!SbaOwns(p))
			return false;
		const size_t k = s_aSbaClass[((BYTE*)p - s_pSbaBase) / kSbaPage];
		SbaLock();
		SbaNode* pNode = (SbaNode*)p;
		pNode->pNext = s_apSbaFree[k];
		s_apSbaFree[k] = pNode;
		SbaUnlock();
		return true;
	}
}

void HHExportsInit()
{
	memset(&s_tempHeap, 0, sizeof(s_tempHeap));
	s_tempHeap.pMemHead = (BYTE*)_aligned_malloc(kTempHeapBytes, 16);
	s_tempHeap.uiCapacity = s_tempHeap.pMemHead ? kTempHeapBytes : 0;
	memset(&s_stringAInit, 0, sizeof(s_stringAInit));
	memset(&s_stringWInit, 0, sizeof(s_stringWInit));
	if(HHGetOptions().sba && !s_pSbaBase)
	{
		s_pSbaBase = (BYTE*)VirtualAlloc(NULL, kSbaReserve, MEM_RESERVE, PAGE_READWRITE);
		s_pSbaNext = s_pSbaBase;
		if(s_pSbaBase)
			HHLog("small-block allocator on: blocks up to %u bytes", (unsigned)kSbaMax);
		else
			HHLogError("small-block allocator: cannot reserve %u MB (error %lu); every block comes from the CRT heap",
			           (unsigned)(kSbaReserve / (1024 * 1024)), GetLastError());
	}
	const unsigned int uSeed = HHGetOptions().heapPerturb;
	if(uSeed && !s_bPerturb)
	{
		InitializeCriticalSection(&s_csPerturb);
		s_uPerturbState = uSeed;
		// Start with a heap full of holes of random sizes.
		for(int i = 0; i < kPerturbSlots; ++i)
		{
			const size_t n = PerturbSize();
			s_apPerturb[i] = malloc(n);
			if(s_apPerturb[i])
				memset(s_apPerturb[i], 0x5A, n);
		}
		for(int i = 0; i < kPerturbSlots * 2; ++i)
			PerturbSlot();
		s_bPerturb = true;
		HHLog("heap-perturb %u: spacer allocations on", uSeed);
	}
}

void HHExportsCheck()
{
	// The temp heap must be fully released whenever the DLL is idle.
	if(s_tempHeap.lLock != 0 || s_tempHeap.lCurrentOffset != 0)
		HHLogError("TempHeap not released: lock %08x offset %u", (unsigned)s_tempHeap.lLock, (unsigned)s_tempHeap.lCurrentOffset);
	if(s_stringAInit.iAllocLength || s_stringAInit.iDataLength || s_stringAInit.sz[0] ||
	   s_stringWInit.iAllocLength || s_stringWInit.iDataLength || s_stringWInit.wsz[0])
		HHLogError("FString empty-string data was modified");
}

std::vector<HHExportRecord> HHGetExportRecords()
{
	std::vector<HHExportRecord> out;
	for(int i = 0; i < NUM_EXPORTS; ++i)
	{
		HHExportRecord r;
		r.name = s_aszExportNames[i];
		r.count = s_alExportCounts[i];
		r.phase = s_aszExportPhase[i] ? s_aszExportPhase[i] : "";
		out.push_back(r);
	}
	return out;
}

extern "C"
{
	// Job system (unused by the game DLL). Signatures follow CivilizationV_Server.exe.
	int __cdecl FJobSetAddJobImpl(void* pJobSet, void* pJob, void* pData)
	{
		UNUSED_VARIABLE(pJobSet); UNUSED_VARIABLE(pJob); UNUSED_VARIABLE(pData);
		UnusedExportHit(EX_FJobSetAddJobImpl);
		return 0;
	}
	void __cdecl FJobSetInitImpl(void** ppJobSet)
	{
		UnusedExportHit(EX_FJobSetInitImpl);
		if(ppJobSet)
			*ppJobSet = NULL;
	}
	void __cdecl FJobSetUninitImpl(void** ppJobSet)
	{
		UNUSED_VARIABLE(ppJobSet);
		UnusedExportHit(EX_FJobSetUninitImpl);
	}
	void __cdecl FJobSetWaitImpl(void* pJobSet)
	{
		UNUSED_VARIABLE(pJobSet);
		UnusedExportHit(EX_FJobSetWaitImpl);
	}

	// Small-block allocator: disabled unless --sba, every request falls back to the CRT heap. FSBAIsDisabled says disabled
	// either way: FireWorks asks FSBAAllocate all the same, and the games with --sba were checked with that answer.
	FSmallObjectAllocator* __cdecl GetSmallObjectAllocator(bool bCreate)
	{
		UNUSED_VARIABLE(bCreate);
		ExportHit(EX_GetSmallObjectAllocator);
		return reinterpret_cast<FSmallObjectAllocator*>(&s_smallObjectAllocator);
	}
	FSmallObjectAllocatorInitParams* __cdecl GetSmallObjectAllocatorInitParams()
	{
		UnusedExportHit(EX_GetSmallObjectAllocatorInitParams);
		return reinterpret_cast<FSmallObjectAllocatorInitParams*>(&s_smallObjectAllocatorInitParams);
	}
	void* __cdecl FSBAAllocate(size_t nSize)
	{
		ExportHit(EX_FSBAAllocate);
		if(s_bPerturb)
			PerturbStep();
		return s_pSbaBase ? SbaAllocate(nSize) : NULL;
	}
	bool __cdecl FSBADeallocate(void* pBlock)
	{
		ExportHit(EX_FSBADeallocate);
		return SbaFree(pBlock);
	}
	void __cdecl FSBADisable(bool bState)
	{
		ExportHit(EX_FSBADisable);
		// Stays disabled whatever is asked; recorded for the report only.
		UNUSED_VARIABLE(bState);
	}
	bool __cdecl FSBAIsDisabled()
	{
		ExportHit(EX_FSBAIsDisabled);
		return s_lSBADisabled != 0;
	}
	bool __cdecl FSBAIsSmallBlock(void* pBlock, size_t& stSize)
	{
		ExportHit(EX_FSBAIsSmallBlock);
		if(!SbaOwns(pBlock))
			return false;
		stSize = (size_t)s_aSbaClass[((BYTE*)pBlock - s_pSbaBase) / kSbaPage] * kSbaStep;
		return true;
	}
	bool __cdecl FSBAHasAllocatorForSize(size_t tSize)
	{
		ExportHit(EX_FSBAHasAllocatorForSize);
		return s_pSbaBase && tSize <= kSbaMax;
	}
	size_t __cdecl FSBAGetMaxAllocatorSize()
	{
		ExportHit(EX_FSBAGetMaxAllocatorSize);
		return s_pSbaBase ? kSbaMax : 0;
	}
	bool __cdecl FSBAPreventCreation(bool bState)
	{
		ExportHit(EX_FSBAPreventCreation);
		return InterlockedExchange(&s_lSBAPreventCreation, bState ? 1 : 0) != 0;
	}

	void* __cdecl FStringA_GetStringInitData()
	{
		ExportHit(EX_FStringA_GetStringInitData);
		return &s_stringAInit;
	}
	void* __cdecl FStringW_GetStringInitData()
	{
		ExportHit(EX_FStringW_GetStringInitData);
		return &s_stringWInit;
	}

	FIFileSystem* __cdecl GetFileSystemMgr()
	{
		ExportHit(EX_GetFileSystemMgr);
		return &HHFileSystem::Get();
	}
	FILogFileMgr* __cdecl GetLogFileMgr()
	{
		ExportHit(EX_GetLogFileMgr);
		return &HHLogFileMgr::Get();
	}
	FILogFileMgr* __cdecl PeekLogFileMgr()
	{
		ExportHit(EX_PeekLogFileMgr);
		return &HHLogFileMgr::Get();
	}
	void* __cdecl GetMemTrackerForDLL()
	{
		UnusedExportHit(EX_GetMemTrackerForDLL);
		return NULL;
	}
	void* __cdecl GetTempHeap()
	{
		ExportHit(EX_GetTempHeap);
		return &s_tempHeap;
	}

	void __cdecl HHCountInitLocksAssign()
	{
		ExportHit(EX_InitLocksAssign);
	}

	// std::_Init_locks::operator= itself is in HHInitLocks.asm (decorated name).
}
