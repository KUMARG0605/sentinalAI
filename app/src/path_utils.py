import os
import sys
from pathlib import Path


def runtime_base_dir() -> Path:
    """Base directory for source and PyInstaller builds."""
    if getattr(sys, "frozen", False):
        # onefile uses _MEIPASS; onedir uses executable folder.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def project_root() -> Path:
    override = os.getenv("SENTINEL_HOME")
    if override:
        return Path(override).resolve()
    return runtime_base_dir()


def resources_dir() -> Path:
    override = os.getenv("SENTINEL_RESOURCES_DIR")
    if override:
        return Path(override).resolve()

    root = project_root()
    direct = root / "resources"
    internal = root / "_internal" / "resources"
    if direct.exists():
        return direct
    if internal.exists():
        return internal
    return direct


def data_dir() -> Path:
    override = os.getenv("SENTINEL_DATA_DIR")
    if override:
        return Path(override).resolve()
    
    # In frozen PyInstaller mode (e.g. Program Files), use APPDATA to avoid Permission Denied
    if getattr(sys, "frozen", False):
        appdata = os.getenv("APPDATA")
        if appdata:
            path = Path(appdata) / "SentinelAI" / "data"
            path.mkdir(parents=True, exist_ok=True)
            return path
            
    # Dev mode fallback
    path = project_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path
