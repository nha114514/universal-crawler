"""Keep Windows venv launchers and their actual workers in one owned job."""
import ctypes
from ctypes import wintypes
import time


def own_worker(process):
    import win32api
    import win32job
    import win32process

    job = win32job.CreateJobObject(None, "")
    try:
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        win32job.AssignProcessToJobObject(job, int(process._handle))

        # Popen closes the initial thread handle. Find its suspended thread with
        # the public Toolhelp API before allowing the venv launcher to run.
        class ThreadEntry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                        ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for function in (kernel.Thread32First, kernel.Thread32Next):
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
            function.restype = wintypes.BOOL
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            available = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while available:
                if entry.th32OwnerProcessID == process.pid:
                    thread = win32api.OpenThread(2, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                    try:
                        win32process.ResumeThread(thread)
                    finally:
                        thread.Close()
                    return job
                available = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise RuntimeError("Failed to resume startup thread of crawler process")
        finally:
            win32api.CloseHandle(snapshot)
    except Exception:
        job.Close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        raise


def stop_worker(process, job=None):
    if job is not None:
        import win32job
        try:
            win32job.TerminateJobObject(job, 1)
            deadline = time.monotonic() + 10
            while win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)["ActiveProcesses"]:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Crawler subprocess did not stop within the exit timeout")
                time.sleep(0.02)
        finally:
            job.Close()
    elif process.poll() is None:
        process.kill()
    process.wait(timeout=10)
