import json
import os
from pathlib import Path

from app.src.path_utils import data_dir, resources_dir


RESOURCES_DIR = resources_dir()

DEFAULT_STATE = {
    "consent_given": False,
    "background_enabled": True,
    "settings_version": 1,
    "root_folder": "C:/",
    "exclude_paths": [],
    "indexing_in_progress": False,
    "index_completed_once": False,
    "llm_model_path": str(RESOURCES_DIR / "models" / "Llama-3.2-3B-Instruct-Q4_K_M.gguf"),
    "embedding_model_path": str(RESOURCES_DIR / "models" / "all-MiniLM-L6-v2"),
    "piper_primary_model": str(RESOURCES_DIR / "piper_models" / "models" / "en_US-lessac" / "en-us-lessac-low.onnx"),
    "piper_secondary_model": str(RESOURCES_DIR / "piper_models" / "models" / "telugu" / "te_IN-venkatesh-medium.onnx"),
    "assistant_language": "english",
    "vosk_model_path": str(RESOURCES_DIR / "vosk_models" / "vosk-model-small-en-us-0.15"),
    "wake_word": "sentinel",
}


def _state_path() -> Path:
    return data_dir() / "app_state.json"


def load_app_state() -> dict:
    state_file = _state_path()
    if not state_file.exists():
        return dict(DEFAULT_STATE)
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_STATE)
    merged = dict(DEFAULT_STATE)
    merged.update(data)
    return merged


def save_app_state(state: dict) -> None:
    state_file = _state_path()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


def update_app_state(updates: dict) -> dict:
    """Apply updates to the persisted state and return the merged result."""
    state = load_app_state()
    state.update(updates)
    save_app_state(state)
    return state


def _lock_path() -> Path:
    return data_dir() / "indexer.lock"


def get_index_lock_pid() -> int | None:
    lock_file = _lock_path()
    if not lock_file.exists():
        return None
    try:
        return int(lock_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def try_acquire_index_lock() -> bool:
    lock_file = _lock_path()
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    if lock_file.exists():
        try:
            pid_text = lock_file.read_text(encoding="utf-8").strip()
            pid = int(pid_text)
            # If process no longer exists, clear stale lock.
            try:
                os.kill(pid, 0)
            except PermissionError:
                # Process exists but we cannot signal it.
                pass
            except OSError:
                lock_file.unlink(missing_ok=True)
        except Exception:
            # Corrupt lock file: clear it.
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False


def release_index_lock() -> None:
    lock_file = _lock_path()
    if lock_file.exists():
        try:
            lock_file.unlink()
        except Exception:
            pass


def _assistant_lock_path() -> Path:
    return data_dir() / "assistant.lock"


def try_acquire_assistant_lock() -> bool:
    lock_file = _assistant_lock_path()
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    if lock_file.exists():
        try:
            pid_text = lock_file.read_text(encoding="utf-8").strip()
            pid = int(pid_text)
            try:
                os.kill(pid, 0)
            except PermissionError:
                pass
            except OSError:
                lock_file.unlink(missing_ok=True)
        except Exception:
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False


def release_assistant_lock() -> None:
    lock_file = _assistant_lock_path()
    if lock_file.exists():
        try:
            lock_file.unlink()
        except Exception:
            pass
