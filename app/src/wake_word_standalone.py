#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
wake_word_standalone.py — Detached Wake Word + Orchestrator Pipeline

Starts automatically when EXE runs (--background mode) and runs forever.
UI controls it via flag files:
  • Create APPDATA/SentinelAI/data/wake_word.stop  → process stops
  • Delete that file + respawn this script          → process restarts

Also auto-starts the background indexer on launch.

PIPELINE per wake event:
  Vosk wake word → beep → AssemblyAI STT → Orchestrator.run()
  → Piper TTS speak → resume listening
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

# ── Project root (frozen exe + dev) ──────────────────────────────────────────
if getattr(sys, "frozen", False):
    _MEIPASS = getattr(sys, "_MEIPASS", None)
    _APP_DIR = Path(_MEIPASS) if _MEIPASS else Path(sys.executable).parent
else:
    _APP_DIR = Path(__file__).resolve().parents[2]

if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import dotenv
dotenv.load_dotenv(_APP_DIR / ".env")


def _setup_log() -> logging.Logger:
    try:
        from app.src.path_utils import data_dir
        log_dir = data_dir().parent / "logs"
    except Exception:
        appdata = os.getenv("APPDATA", str(Path.home()))
        log_dir = Path(appdata) / "SentinelAI" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "wake.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    return logging.getLogger("wake_standalone")

log = _setup_log()

from app.src.index_runtime import (
    clear_wake_stop,
    is_wake_stop_requested,
    write_wake_status,
    clear_index_stop,
    is_index_stop_requested,
)


# ─────────────────────────────────────────────────────────────────────────────
#  AUTO-START INDEXER in daemon thread
# ─────────────────────────────────────────────────────────────────────────────

def _auto_start_indexer() -> None:
    """Start indexer in background if not already running. Called once on startup."""
    try:
        from app.ui.state import load_app_state, try_acquire_index_lock, release_index_lock
        from app.src.path_utils import data_dir

        state = load_app_state()
        if not state.get("consent_given", False):
            log.info("[Indexer] Consent not given — skipping.")
            return
        if not try_acquire_index_lock():
            log.info("[Indexer] Already running — skipping.")
            return

        root_folder   = state.get("root_folder", "C:/")
        exclude_paths = state.get("exclude_paths", [])
        index_path    = str(data_dir() / "faiss_index")
        log.info(f"[Indexer] Starting: root={root_folder}")
        clear_index_stop()

        try:
            from app.src.indexer import create_index
            result = create_index(
                data_folder=root_folder,
                index_path=index_path,
                exclude_paths=exclude_paths,
                max_workers=1,
                batch_size=1,
                stop_requested=is_index_stop_requested,
            )
            log.info(f"[Indexer] Done: {result.get('status','?')} ({result.get('new_files',0)} new files)")
        finally:
            release_index_lock()
    except Exception as e:
        log.exception(f"[Indexer] Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  BEEP
# ─────────────────────────────────────────────────────────────────────────────

def _play_beep() -> None:
    try:
        import numpy as np
        import sounddevice as sd
        sr   = 22050
        t    = np.linspace(0, 0.18, int(sr * 0.18))
        tone = (np.sin(2 * 3.14159 * 880 * t) * 0.28).astype("float32")
        fade = 40
        tone[:fade]  = tone[:fade] * [i / fade for i in range(fade)]
        tone[-fade:] = tone[-fade:] * [1 - i / fade for i in range(fade)]
        sd.play(tone, sr, blocking=True)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  TTS
# ─────────────────────────────────────────────────────────────────────────────

def _speak(text: str) -> None:
    if not text:
        return
    try:
        from app.src.voice_pipeline import speak_text
        first = text.split(".")[0].strip()
        speak_text((first if len(first) >= 8 else text)[:300])
    except Exception as e:
        log.warning(f"TTS: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  ORCHESTRATOR (lazy-loaded on first wake)
# ─────────────────────────────────────────────────────────────────────────────

_orch      = None
_orch_lock = threading.Lock()


def _get_orchestrator():
    global _orch
    with _orch_lock:
        if _orch is not None:
            return _orch
        log.info("[Orch] Loading…")
        try:
            from app.src.orchestrator import build_orchestrator
            _orch = build_orchestrator(
                on_progress=lambda m: log.info(f"  {m}") if any(
                    k in m for k in ["Submitting", "✓", "✗", "Done in"]) else None
            )
            log.info("[Orch] Ready.")
        except Exception as e:
            log.exception(f"[Orch] Load failed: {e}")
            _orch = None
    return _orch


# ─────────────────────────────────────────────────────────────────────────────
#  CHECKPOINTER per conversation session
# ─────────────────────────────────────────────────────────────────────────────

def _new_thread_id() -> str:
    try:
        from app.src.checkpointer import new_thread_id
        return new_thread_id()
    except Exception:
        return f"wake-{uuid.uuid4().hex[:10]}"


def _run_orch(prompt: str, thread_id: str) -> str:
    orch = _get_orchestrator()
    if orch is None:
        return "Sorry, the AI system is not available right now."
    try:
        # Attach thread_id to orchestrator memory so checkpointer can scope it
        if hasattr(orch, "memory") and hasattr(orch.memory, "_thread_id"):
            orch.memory._thread_id = thread_id
        result = orch.run(prompt)
        return result.get("answer", "") if isinstance(result, dict) else str(result)
    except Exception as e:
        log.exception(f"[Orch] run error: {e}")
        return f"Sorry, I ran into an error: {e}"


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    from app.src.voice_pipeline import (
        start_wake_word_process,
        wait_for_wake_word,
        resume_wake_word,
        stop_wake_word,
    )
    from app.src.stt import transcribe_mic
    from app.src.config import wake_word as get_wake_word

    clear_wake_stop()
    write_wake_status("starting", pid=os.getpid())
    configured_wake = get_wake_word() or "sentinel"

    log.info(f"=== Wake Word Standalone  pid={os.getpid()} ===")
    log.info(f"Wake word: '{configured_wake}'")

    # Auto-start indexer in background
    threading.Thread(target=_auto_start_indexer, daemon=True, name="auto-indexer").start()

    log.info("Starting Vosk wake word detector…")
    wake_proc = start_wake_word_process()

    # Each session (wake → conversation end) gets its own checkpointer thread
    session_id = _new_thread_id()

    try:
        while True:
            # Check stop flag every loop
            if is_wake_stop_requested():
                log.info("Stop flag found — exiting.")
                write_wake_status("stopped", pid=os.getpid())
                break

            write_wake_status("listening", pid=os.getpid())
            log.info(f"Listening for '{configured_wake}'…")

            try:
                detected = wait_for_wake_word(wake_proc)
            except RuntimeError as e:
                log.error(f"Wake detector error: {e}")
                write_wake_status("error", pid=os.getpid(), extra={"error": str(e)})
                time.sleep(3)
                continue

            if not detected:
                continue

            # Wake word heard
            log.info("Wake word detected!")
            write_wake_status("wake_detected", pid=os.getpid())
            _play_beep()

            # STT
            write_wake_status("transcribing", pid=os.getpid())

            def _partial(t: str):
                print(f"  ▶ {t}    ", end="\r", flush=True)

            transcript = ""
            try:
                transcript = transcribe_mic(on_partial=_partial)
            except Exception as e:
                log.error(f"STT error: {e}")

            print()

            if not transcript:
                log.info("No speech — resuming.")
                _speak("Sorry, I didn't catch that.")
                resume_wake_word(wake_proc)
                continue

            log.info(f"Transcript: {transcript}")

            # Orchestrator
            write_wake_status("thinking", pid=os.getpid(), extra={"prompt": transcript[:80]})
            answer = _run_orch(transcript, session_id)
            log.info(f"Answer: {answer[:120]}")

            # Speak
            write_wake_status("speaking", pid=os.getpid())
            _speak(answer)

            # New session ID for next wake (fresh conversation context)
            session_id = _new_thread_id()

            resume_wake_word(wake_proc)

    except KeyboardInterrupt:
        log.info("KeyboardInterrupt.")
    except Exception as e:
        log.exception(f"Fatal: {e}")
        write_wake_status("error", pid=os.getpid(), extra={"error": str(e)})
    finally:
        try:
            stop_wake_word(wake_proc)
        except Exception:
            pass
        write_wake_status("stopped", pid=os.getpid())
        log.info("Exited.")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        log.exception(f"Startup failed: {e}")
        sys.exit(1)