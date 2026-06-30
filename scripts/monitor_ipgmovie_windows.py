#!/usr/bin/env python
"""
Monitor for IPGMovie error windows and log their appearance.
Run this before starting calibration, then run calibration as usual.
"""
import time
import win32gui
import win32process
import psutil
from datetime import datetime
from collections import defaultdict


def get_ipgmovie_processes():
    """Get all IPGMovie.exe processes."""
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['name'] and 'movie' in proc.info['name'].lower():
                processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return processes


def get_windows_by_pid(pid):
    """Get all windows belonging to a specific PID."""
    windows = []

    def callback(hwnd, lParam):
        if win32gui.IsWindowVisible(hwnd):
            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
            if found_pid == pid:
                title = win32gui.GetWindowText(hwnd)
                windows.append((hwnd, title))
        return True

    win32gui.EnumWindows(callback, None)
    return windows


def monitor_windows():
    """Main monitoring loop."""
    print("=" * 80)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] IPGMovie Window Monitor Started")
    print("=" * 80)
    print("\nWatching for IPGMovie error windows...")
    print("Press Ctrl+C to stop\n")

    # Track known windows
    known_windows = set()
    last_check = time.time()

    try:
        while True:
            time.sleep(0.5)  # Check every 500ms

            # Get all Movie processes
            movie_procs = get_ipgmovie_processes()

            for proc in movie_procs:
                try:
                    windows = get_windows_by_pid(proc.pid)
                    for hwnd, title in windows:
                        window_key = (hwnd, title)

                        # Check for error-related windows
                        if window_key not in known_windows:
                            known_windows.add(window_key)

                            # Log all new windows (especially errors/errors dialogs)
                            timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                            # Check if it's a dialog or error window
                            window_class = win32gui.GetClassName(hwnd)
                            is_dialog = 'dialog' in window_class.lower() or '#32770' in window_class
                            is_error = any(kw in title.lower() for kw in ['error', 'fail', 'exception', 'dll'])

                            if is_dialog or is_error or title:  # Log dialogs, errors, or any titled window
                                print(f"[{timestamp}] NEW WINDOW DETECTED")
                                print(f"  PID: {proc.pid}")
                                print(f"  Title: {title or '(no title)'}")
                                print(f"  Class: {window_class}")
                                print(f"  Process: {proc.info['name']}")
                                print(f"  Command: {' '.join(proc.info.get('cmdline', [])[:5])}")
                                print()

                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # Clean up stale windows
            current_time = time.time()
            if current_time - last_check > 5:  # Clean every 5 seconds
                stale = []
                for window_key in known_windows:
                    hwnd, _ = window_key
                    if not win32gui.IsWindow(hwnd):
                        stale.append(window_key)
                for key in stale:
                    known_windows.remove(key)
                last_check = current_time

    except KeyboardInterrupt:
        print("\n" + "=" * 80)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Monitor Stopped")
        print("=" * 80)


if __name__ == "__main__":
    try:
        import win32gui
        import win32process
        import psutil
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("\nInstall required packages:")
        print("  pip install pywin32 psutil")
        exit(1)

    monitor_windows()
