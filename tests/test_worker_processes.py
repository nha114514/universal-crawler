import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from crawler_system.config import python_command
from crawler_system.errors import CrawlerError
from crawler_system.jobs import run_worker


@unittest.skipUnless(os.name == "nt", "Windows process ownership regression")
class WorkerProcessTests(unittest.TestCase):
    def test_transient_windows_file_lock_does_not_replace_primary_timeout(self):
        from crawler_system.jobs import worker_directory
        import win32con
        import win32file
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / 'work'
            work.mkdir()
            holder = None
            releaser = None
            try:
                with self.assertRaises(CrawlerError) as caught:
                    with worker_directory(work) as owned:
                        path = Path(owned) / 'stderr.log'
                        path.write_text('owned diagnostic', encoding='utf-8')
                        holder = win32file.CreateFile(str(path), win32con.GENERIC_READ,
                                                     win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
                                                     None, win32con.OPEN_EXISTING, 0, None)
                        releaser = threading.Timer(0.4, holder.Close)
                        releaser.start()
                        raise CrawlerError('TIMEOUT', 'primary error')
                self.assertEqual(caught.exception.code, 'TIMEOUT')
                releaser.join(2)
                self.assertEqual(list(work.iterdir()), [])
            finally:
                if releaser:
                    releaser.join(2)

    def check_termination(self, cancelled):
        import win32api
        import win32con
        import win32event

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_file = root / "child.pid"
            parent_file = root / "parent.pid"
            child = "import os,time,pathlib;pathlib.Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()));time.sleep(60)"
            program = "import os,pathlib,subprocess,time;pathlib.Path(" + repr(str(parent_file)) + ").write_text(str(os.getpid()));subprocess.Popen([" + repr(python_command()) + ",'-c'," + repr(child) + "]);time.sleep(60)"
            real_popen = subprocess.Popen
            owned_handles = []

            def launch_fixture(command, **options):
                return real_popen([command[0], "-c", program], **options)

            cancel = threading.Event()

            def observe():
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not pid_file.exists():
                    time.sleep(0.02)
                if pid_file.exists():
                    for path in (pid_file, parent_file):
                        pid = int(path.read_text())
                        owned_handles.append(win32api.OpenProcess(win32con.SYNCHRONIZE | win32con.PROCESS_TERMINATE, False, pid))
                if cancelled:
                    cancel.set()

            watcher = threading.Thread(target=observe)
            watcher.start()
            try:
                with patch("crawler_system.jobs.DATA", root), patch("crawler_system.jobs.settings", return_value={"worker_timeout": 2}), patch("crawler_system.jobs.subprocess.Popen", side_effect=launch_fixture):
                    try:
                        run_worker({"input": "https://example.org"}, cancel)
                    except Exception as error:
                        self.assertIsInstance(error, CrawlerError)
                        self.assertEqual(error.code, "CANCELLED" if cancelled else "TIMEOUT")
                    else:
                        self.fail("The sleeping worker should terminate")
                watcher.join(6)
                self.assertEqual(len(owned_handles), 2, "Fixture parent and child must have actually started")
                self.assertEqual(win32event.WaitForMultipleObjects(owned_handles, True, 1000), win32event.WAIT_OBJECT_0, "All worker descendants must exit before cleanup")
                self.assertEqual(list((root / "work").iterdir()), [])
            finally:
                watcher.join(6)
                for handle in owned_handles:
                    if win32event.WaitForSingleObject(handle, 0) != win32event.WAIT_OBJECT_0:
                        win32api.TerminateProcess(handle, 1)
                        win32event.WaitForSingleObject(handle, 3000)
                    handle.Close()

    def test_timeout_stops_actual_python_child_before_cleanup(self):
        self.check_termination(False)

    def test_cancellation_stops_actual_python_child_before_cleanup(self):
        self.check_termination(True)
