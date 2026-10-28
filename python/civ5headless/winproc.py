"""Windows process helpers for the host tooling (ctypes only; no psutil).

- Job objects: per-run memory limit, CPU affinity, CPU cap (hard rate control), kill-on-close (a killed runner takes
  its hosts with it), job accounting (CPU time, peak commit).
- Process priority: below normal, or efficiency mode (EcoQoS and idle priority, as Task Manager's).
- Process statistics: CPU seconds, peak working set, peak/current private bytes, peak virtual size.
- CPU topology: physical cores with their efficiency class (P-cores first on hybrid CPUs such as the 13900K).
- File replace: os.replace retried through a passing sharing violation (a scanner or reader holding the file).
"""
import ctypes
import os
import struct
import time
from ctypes import wintypes

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)
ntdll = ctypes.WinDLL('ntdll')

JOB_OBJECT_LIMIT_AFFINITY = 0x10
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectBasicAccountingInformation = 1
JobObjectExtendedLimitInformation = 9
JobObjectCpuRateControlInformation = 15
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4
BELOW_NORMAL_PRIORITY_CLASS = 0x4000
IDLE_PRIORITY_CLASS = 0x40
ProcessPowerThrottling = 4
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
ALL_PROCESSOR_GROUPS = 0xFFFF
CREATE_NO_WINDOW = 0x08000000


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                                   'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION), ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [('TotalUserTime', ctypes.c_longlong), ('TotalKernelTime', ctypes.c_longlong),
                ('ThisPeriodTotalUserTime', ctypes.c_longlong), ('ThisPeriodTotalKernelTime', ctypes.c_longlong),
                ('TotalPageFaultCount', wintypes.DWORD), ('TotalProcesses', wintypes.DWORD),
                ('ActiveProcesses', wintypes.DWORD), ('TotalTerminatedProcesses', wintypes.DWORD)]


class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
    _fields_ = [('ControlFlags', wintypes.DWORD), ('CpuRate', wintypes.DWORD)]  # CpuRate: a union with Weight/MinMaxRate


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [('Version', wintypes.ULONG), ('ControlMask', wintypes.ULONG), ('StateMask', wintypes.ULONG)]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD), ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t), ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t), ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t), ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t), ('PrivateUsage', ctypes.c_size_t)]


class VM_COUNTERS(ctypes.Structure):
    _fields_ = [('PeakVirtualSize', ctypes.c_size_t), ('VirtualSize', ctypes.c_size_t), ('PageFaultCount', wintypes.ULONG),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]


kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetPriorityClass.argtypes = [wintypes.HANDLE]
kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
kernel32.GetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
kernel32.GetActiveProcessorCount.restype = wintypes.DWORD
kernel32.GetActiveProcessorCount.argtypes = [wintypes.WORD]
kernel32.GetLogicalProcessorInformationEx.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p]


def logical_cpus():
    """Logical processors of the machine, every processor group."""
    return kernel32.GetActiveProcessorCount(ALL_PROCESSOR_GROUPS) or os.cpu_count() or 1


def cpu_rate(percent, within=None):
    """A job CpuRate for percent of one logical processor: cycles per 10,000 of the whole machine, or of the parent
    job's cap for a job nested in one capped at within percent."""
    return max(1, min(10000, round(float(percent) * 10000 / (within or 100 * logical_cpus()))))


class Job:
    """A job object holding host processes: memory limit, affinity, CPU cap, kill on close.

    cpu_limit: percent of one logical processor (the host is single-threaded), a hard cap: past its share of a
    scheduling interval the job's threads wait for the next one. within: the capped Job this one is nested in (its
    processes are assigned to that one first), whose cap a nested job's rate is counted against."""

    def __init__(self, memory_mb=None, affinity=None, cpu_limit=None, within=None):
        self.cpu_limit = cpu_limit
        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        if memory_mb:
            flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = int(memory_mb) * 2 ** 20
        if affinity:
            flags |= JOB_OBJECT_LIMIT_AFFINITY
            info.BasicLimitInformation.Affinity = int(affinity)
        info.BasicLimitInformation.LimitFlags = flags
        if not kernel32.SetInformationJobObject(self.handle, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if cpu_limit:
            rate = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP,
                                                          cpu_rate(cpu_limit, within and within.cpu_limit))
            if not kernel32.SetInformationJobObject(self.handle, JobObjectCpuRateControlInformation, ctypes.byref(rate), ctypes.sizeof(rate)):
                raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, popen):
        if not kernel32.AssignProcessToJobObject(self.handle, int(popen._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self, code=1):
        kernel32.TerminateJobObject(self.handle, code)

    def stats(self):
        ext = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        acc = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        kernel32.QueryInformationJobObject(self.handle, JobObjectExtendedLimitInformation, ctypes.byref(ext), ctypes.sizeof(ext), None)
        kernel32.QueryInformationJobObject(self.handle, JobObjectBasicAccountingInformation, ctypes.byref(acc), ctypes.sizeof(acc), None)
        return dict(peakProcessCommit=ext.PeakProcessMemoryUsed, memoryLimit=ext.ProcessMemoryLimit or None,
                    cpuSeconds=(acc.TotalUserTime + acc.TotalKernelTime) / 1e7)

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


def process_stats(popen):
    """CPU and memory of a (possibly exited) Popen process; the handle stays valid until the Popen is collected."""
    h = int(popen._handle)
    c, e, k, u = (ctypes.c_ulonglong() for _ in range(4))
    out = {}
    if kernel32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
        out['CPUSeconds'] = (k.value + u.value) / 1e7
        out['StartTicks'] = c.value  # FILETIME (100 ns since 1601), like .NET's StartTime ticks offset
    m = PROCESS_MEMORY_COUNTERS_EX()
    m.cb = ctypes.sizeof(m)
    if psapi.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb):
        out.update(PeakWorkingSet=m.PeakWorkingSetSize, WorkingSet=m.WorkingSetSize, PrivateBytes=m.PrivateUsage,
                   PeakPrivateBytes=m.PeakPagefileUsage)
    vm = VM_COUNTERS()
    if ntdll.NtQueryInformationProcess(h, 3, ctypes.byref(vm), ctypes.sizeof(vm), None) == 0:
        out['PeakVirtual'] = vm.PeakVirtualSize
    return out


def set_priority(popen, below_normal=True):
    if below_normal:
        kernel32.SetPriorityClass(int(popen._handle), BELOW_NORMAL_PRIORITY_CLASS)


def set_efficiency_mode(popen):
    """Task Manager's efficiency mode: EcoQoS (lower clocks, efficient cores on hybrid CPUs) and idle priority.
    False where Windows has no EcoQoS (before Windows 10 1709); the priority is set either way."""
    h = int(popen._handle)
    kernel32.SetPriorityClass(h, IDLE_PRIORITY_CLASS)
    state = PROCESS_POWER_THROTTLING_STATE(PROCESS_POWER_THROTTLING_CURRENT_VERSION, PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
                                           PROCESS_POWER_THROTTLING_EXECUTION_SPEED)
    return bool(kernel32.SetProcessInformation(h, ProcessPowerThrottling, ctypes.byref(state), ctypes.sizeof(state)))


def efficiency_mode(popen):
    """(EcoQoS on, priority class) of a running process."""
    h = int(popen._handle)
    state = PROCESS_POWER_THROTTLING_STATE(PROCESS_POWER_THROTTLING_CURRENT_VERSION, 0, 0)
    ok = kernel32.GetProcessInformation(h, ProcessPowerThrottling, ctypes.byref(state), ctypes.sizeof(state))
    return bool(ok and state.ControlMask & state.StateMask & PROCESS_POWER_THROTTLING_EXECUTION_SPEED), kernel32.GetPriorityClass(h)


def cores():
    """Physical cores as [(efficiencyClass, affinityMask)] for processor group 0, fastest class first."""
    n = wintypes.DWORD(0)
    kernel32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(n))
    buf = ctypes.create_string_buffer(n.value)
    if not kernel32.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(n)):
        return []
    raw = buf.raw[:n.value]
    out, off = [], 0
    while off < len(raw):
        rel, size = struct.unpack_from('<II', raw, off)
        if rel == 0:  # RelationProcessorCore
            eff = raw[off + 9]
            groups = struct.unpack_from('<H', raw, off + 30)[0]
            for g in range(groups):
                mask, group = struct.unpack_from('<QH', raw, off + 32 + 16 * g)
                if group == 0:
                    out.append((eff, mask))
        off += size
    out.sort(key=lambda x: (-x[0], x[1] & -x[1]))
    return out


def worker_affinities(workers, mode='p-first'):
    """One affinity mask per worker slot: P-cores (both hyperthreads) first, then E-cores, then no affinity.

    mode 'none' gives no affinity to anyone."""
    if mode == 'none':
        return [None] * workers
    cs = cores()
    masks = [mask for _, mask in cs]
    return [masks[i] if i < len(masks) else None for i in range(workers)]


def replace(src, dst, tries=5, delay=0.05):
    """os.replace, retried on PermissionError: a scanner (Defender) or a reader holding src or dst open without delete
    sharing denies it for a moment (WinError 5). Backs off 0.05, 0.1, 0.2, 0.4 s (0.75 s in all), then raises."""
    for attempt in range(tries):
        try:
            return os.replace(src, dst)
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(delay * 2 ** attempt)


def pid_alive(pid):
    """True if a process with this id is running (PROCESS_QUERY_LIMITED_INFORMATION + exit code check)."""
    h = kernel32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return False
    code = wintypes.DWORD()
    ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    kernel32.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]


def listening_pids(port):
    """Ids of the processes listening on TCP port (IPv4), from GetExtendedTcpTable (TCP_TABLE_OWNER_PID_LISTENER)."""
    iphlpapi = ctypes.WinDLL('iphlpapi')
    size = wintypes.DWORD(0)
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0)  # AF_INET, TCP_TABLE_OWNER_PID_LISTENER
    buf = ctypes.create_string_buffer(size.value + 4096)
    size = wintypes.DWORD(len(buf))
    if iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, 2, 3, 0) != 0:
        return []
    count = struct.unpack_from('<I', buf.raw, 0)[0]
    out = []
    for i in range(count):  # MIB_TCPROW_OWNER_PID: state, local addr, local port, remote addr, remote port, pid
        _, _, local_port, _, _, pid = struct.unpack_from('<IIIIII', buf.raw, 4 + 24 * i)
        if ((local_port & 0xFF) << 8 | (local_port >> 8) & 0xFF) == port:
            out.append(pid)
    return out


# ---- background load (measurements must say what else ran) ----
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
                ('szExeFile', ctypes.c_wchar * 260)]


kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.GetSystemTimes.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)] * 3


def pids(prefix):
    """Process ids whose exe name starts with prefix (case-insensitive)."""
    snap = kernel32.CreateToolhelp32Snapshot(2, 0)
    e = PROCESSENTRY32W(); e.dwSize = ctypes.sizeof(e)
    out = []
    ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.lower().startswith(prefix.lower()):
            out.append(e.th32ProcessID)
        ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    kernel32.CloseHandle(snap)
    return out


def pid_cpu_seconds(pid):
    h = kernel32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return None
    c, e, k, u = (ctypes.c_ulonglong() for _ in range(4))
    ok = kernel32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u))
    kernel32.CloseHandle(h)
    return (k.value + u.value) / 1e7 if ok else None


class LoadMeter:
    """System busy cores and the real game's CPU between start() and stop()."""

    def __init__(self, game_prefix='CivilizationV'):
        self.game_prefix = game_prefix

    def _sample(self):
        idle, kern, user = (ctypes.c_ulonglong() for _ in range(3))
        kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        game = {p: pid_cpu_seconds(p) for p in pids(self.game_prefix)}
        return time.monotonic(), idle.value, kern.value + user.value, game

    def start(self):
        self.a = self._sample()
        return self

    def stop(self):
        t1, i1, b1, g1 = self._sample()
        t0, i0, b0, g0 = self.a
        wall = max(t1 - t0, 1e-6)
        busy = ((b1 - b0) - (i1 - i0)) / 1e7 / wall  # kernel time includes idle
        # A game started during the window counts in full; one that exited during it is missed (the matrix
        # recorder relaunches the game per run, so gameBusyCores is a lower bound; otherBusyCores is not).
        game = sum((g1[p] or 0) - (g0.get(p) or 0) for p in g1) / wall
        return dict(seconds=round(wall, 1), logicalCPUs=os.cpu_count(), systemBusyCores=round(busy, 2),
                    gameRunning=bool(g1), gameBusyCores=round(game, 2))


if __name__ == '__main__':
    for eff, mask in cores():
        print(f'efficiency class {eff}: mask {mask:#x}')
