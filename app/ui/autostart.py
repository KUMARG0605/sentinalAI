import os
import sys


RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "SentinelAIIndexer"


def _build_command() -> str:
    if getattr(sys, "frozen", False):
        return f"\"{sys.executable}\" --background"
    return f"\"{sys.executable}\" -m app.ui.main --background"

def _is_our_background_process(cmd: list[str] | None) -> bool:
    if not cmd:
        return False
    # Join into a single string — frozen exes launched via Start-Process may
    # present the entire command as one token, so element-level checks miss it.
    cmd_text = " ".join(str(part).strip() for part in cmd if part).lower()
    if "--background" not in cmd_text:
        return False

    if getattr(sys, "frozen", False):
        target_exe = os.path.basename(sys.executable).lower()
        exe_names = {target_exe, "sentinelai.exe"}
        return any(name in cmd_text for name in exe_names)
    return ("app.ui.main" in cmd_text) or ("app/ui/main.py" in cmd_text) or ("app\\ui\\main.py" in cmd_text)


def find_background_agent_pids(exclude_current: bool = True) -> list[int]:
    import psutil

    current_pid = os.getpid()
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = int(proc.info.get("pid", 0))
            if exclude_current and pid == current_pid:
                continue
            cmd = proc.info.get("cmdline")
            if _is_our_background_process(cmd):
                pids.append(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, ValueError, TypeError):
            continue
    return pids


def is_background_agent_running() -> bool:
    return len(find_background_agent_pids(exclude_current=True)) > 0


def stop_background_agents() -> int:
    import re
    import subprocess

    if os.name != "nt":
        return 0

    # Use the same two PowerShell commands used manually in troubleshooting.
    query_cmd = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match '--background|wake_word_standalone\\.py' -and $_.Name -match 'SentinelAI|python' } | "
        "Select-Object ProcessId, Name, CommandLine"
    )
    stop_cmd = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match '--background|wake_word_standalone\\.py' -and $_.Name -match 'SentinelAI|python' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )

    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", query_cmd],
        capture_output=True,
        text=True,
    )
    pids = set()
    for line in proc.stdout.splitlines():
        m = re.match(r"^\s*(\d+)\s+", line)
        if m:
            pids.add(int(m.group(1)))

    subprocess.run(
        ["powershell", "-NoProfile", "-Command", stop_cmd],
        capture_output=True,
        text=True,
    )
    return len(pids)


def enable_background_autostart() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, _build_command())
        return True
    except Exception:
        return False


def disable_background_autostart() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
        return True
    except Exception:
        return False

def launch_background_agent_if_not_running():
    """Detect if the background daemon is running, and if not, spawn it cleanly via PowerShell."""
    import subprocess
    import tempfile
    import time

    # Cooldown guard — prevents double-spawning during the ~3s window between
    # Start-Process returning and the new process appearing in the process list.
    _lock_file = os.path.join(tempfile.gettempdir(), "sentinel_bg_launch.lock")
    try:
        if os.path.exists(_lock_file):
            if time.time() - os.path.getmtime(_lock_file) < 8:
                return  # A launch was initiated less than 8s ago, skip
        open(_lock_file, "w").close()  # Touch the lock file
    except OSError:
        pass

    is_running = is_background_agent_running()
    if is_running:
        return

    print("[AUTOSTART] Launching detached background agent via PowerShell...")
    if getattr(sys, "frozen", False):
        exe = sys.executable
        args = "--background"
    else:
        exe = sys.executable
        args = "-m app.ui.main --background"

    # Launch via PowerShell Start-Process to ensure it has native UI privileges (like standard taskbar mic icons)
    ps_cmd = f"Start-Process -FilePath '{exe}' -ArgumentList '{args}' -WindowStyle Hidden"

    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            creationflags=0x08000000,  # CREATE_NO_WINDOW for the short-lived launcher powershell only
            close_fds=True
        )
    except Exception as e:
        print(f"[ERROR] Failed to spawn background agent: {e}")
