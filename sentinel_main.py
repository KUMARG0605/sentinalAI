"""
sentinel_main.py — SentinelAI v2 Entry Point

Architecture:
  ┌─────────────────────────────────────────────────────┐
  │  Wake Word Process (DETACHED, always running)       │
  │  Vosk offline model → listens for "Sentinel"        │
  │  Runs in background thread, independent of UI       │
  └────────────────┬────────────────────────────────────┘
                   │ wake event
  ┌────────────────▼────────────────────────────────────┐
  │  AssemblyAI STT — stream mic until end-of-turn      │
  │  Returns clean transcript text                      │
  └────────────────┬────────────────────────────────────┘
                   │ prompt string
  ┌────────────────▼────────────────────────────────────┐
  │  Orchestrator.run(prompt)                           │
  │  DAG → agents → answer                             │
  └─────────────────────────────────────────────────────┘

Usage:
    python sentinel_main.py               # console mode (no UI)
    python sentinel_main.py --ui          # launch full UI (default)
    python sentinel_main.py --voice-only  # wake word + voice, no GUI
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

# ── ensure project root on path ───────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
dotenv.load_dotenv(PROJECT_ROOT / ".env")


# ─────────────────────────────────────────────────────────────────────────────
#  WAKE-WORD  →  STT  →  ORCHESTRATOR  PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

class VoiceAgentPipeline:
    """
    Connects the detached wake word detector to the orchestrator.

    The wake word process runs in its own daemon thread and is completely
    independent of the UI.  When it signals a wake event:
      1. STT streams the user's speech via AssemblyAI
      2. The transcript is passed directly to orchestrator.run()
      3. The answer is returned (printed / spoken / sent to UI)
      4. Wake word detector resumes listening

    on_prompt_received: optional callback(prompt: str) for UI live display
    on_answer_ready:    optional callback(answer: str) for UI display
    on_partial_stt:     optional callback(partial: str) for live transcript
    on_state_change:    optional callback(state: str) — 'idle'|'wake'|'listening'|'thinking'
    """

    def __init__(
        self,
        orchestrator=None,
        on_prompt_received=None,
        on_answer_ready=None,
        on_partial_stt=None,
        on_state_change=None,
        speak_responses: bool = False,
    ):
        self.orch              = orchestrator
        self.on_prompt         = on_prompt_received
        self.on_answer         = on_answer_ready
        self.on_partial_stt    = on_partial_stt
        self.on_state          = on_state_change
        self.speak             = speak_responses

        self._wake_thread      = None
        self._pipeline_thread  = None
        self._stop_event       = threading.Event()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _set_state(self, state: str):
        print(f"[Pipeline] State → {state}")
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:
                pass

    def _speak(self, text: str):
        if not self.speak:
            return
        try:
            from app.src.voice_pipeline import speak_text
            speak_text(text)
        except Exception as e:
            print(f"[Pipeline] TTS failed: {e}")

    # ── main loop ─────────────────────────────────────────────────────────────

    def _run_loop(self):
        """Main pipeline loop — runs in its own thread."""
        from app.src.voice_pipeline import (
            start_wake_word_process,
            wait_for_wake_word,
            resume_wake_word,
            stop_wake_word,
        )
        from app.src.stt import transcribe_mic
        from app.src.config import wake_word as get_wake_word

        configured_wake = get_wake_word() or "sentinel"
        print(f"[Pipeline] Wake word: '{configured_wake}'")

        # Start detached wake word thread
        print("[Pipeline] Starting detached wake word detector...")
        wake_proc = start_wake_word_process()
        self._wake_proc = wake_proc
        self._set_state("idle")

        try:
            while not self._stop_event.is_set():
                # ── IDLE: wait for wake word ──────────────────────────────────
                print(f"\n[Pipeline] 🎙  Listening for wake word ('{configured_wake}')...")
                try:
                    detected = wait_for_wake_word(wake_proc)
                except RuntimeError as e:
                    print(f"[Pipeline] Wake word error: {e}")
                    break

                if not detected or self._stop_event.is_set():
                    break

                # ── WAKE: greet + listen ──────────────────────────────────────
                self._set_state("wake")
                print("[Pipeline] ✅ Wake word detected!")
                self._speak("Yes, I'm listening.")

                self._set_state("listening")
                print("[Pipeline] 🎤 Listening for command...")

                # Stream mic → AssemblyAI → final transcript
                transcript = transcribe_mic(on_partial=self._on_partial)

                if not transcript:
                    print("[Pipeline] No speech detected — resuming wake word.")
                    self._speak("Sorry, I didn't catch that.")
                    resume_wake_word(wake_proc)
                    self._set_state("idle")
                    continue

                print(f"[Pipeline] 📝 Prompt: {transcript}")

                # ── THINKING: pass to orchestrator ────────────────────────────
                self._set_state("thinking")
                if self.on_prompt:
                    try:
                        self.on_prompt(transcript)
                    except Exception:
                        pass

                answer = self._run_orchestrator(transcript)

                # ── ANSWER: deliver result ────────────────────────────────────
                print(f"[Pipeline] 💬 Answer: {answer[:200]}")
                if self.on_answer:
                    try:
                        self.on_answer(answer)
                    except Exception:
                        pass

                # Speak a short summary (TTS)
                if self.speak and answer:
                    brief = answer[:300].split("\n")[0]  # first line only
                    self._speak(brief)

                # ── RESUME: go back to wake word ──────────────────────────────
                resume_wake_word(wake_proc)
                self._set_state("idle")

        except Exception as e:
            print(f"[Pipeline] Fatal error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            try:
                stop_wake_word(wake_proc)
            except Exception:
                pass
            self._set_state("idle")
            print("[Pipeline] Stopped.")

    def _on_partial(self, text: str):
        """Called by STT for every in-progress transcript update."""
        if self.on_partial_stt:
            try:
                self.on_partial_stt(text)
            except Exception:
                pass

    def _run_orchestrator(self, prompt: str) -> str:
        """Pass transcript to orchestrator, return answer string."""
        if self.orch is None:
            return f"[No orchestrator] You said: {prompt}"
        try:
            result = self.orch.run(prompt)
            return result.get("answer", "") if isinstance(result, dict) else str(result)
        except Exception as e:
            print(f"[Pipeline] Orchestrator error: {e}")
            return f"Sorry, I encountered an error: {e}"

    # ── public API ────────────────────────────────────────────────────────────

    def start(self):
        """Start the pipeline in a background thread."""
        self._stop_event.clear()
        self._pipeline_thread = threading.Thread(
            target=self._run_loop, daemon=True, name="sentinel-pipeline"
        )
        self._pipeline_thread.start()
        print("[Pipeline] Started in background thread.")

    def stop(self):
        """Signal the pipeline to stop."""
        self._stop_event.set()
        if hasattr(self, "_wake_proc"):
            try:
                from app.src.voice_pipeline import stop_wake_word
                stop_wake_word(self._wake_proc)
            except Exception:
                pass
        if self._pipeline_thread:
            self._pipeline_thread.join(timeout=5)
        print("[Pipeline] Stopped.")

    def run_blocking(self):
        """Run the pipeline in the current thread (for console-only mode)."""
        self._stop_event.clear()
        self._run_loop()


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def build_orchestrator_with_progress():
    """Build the orchestrator with a simple console progress printer."""
    from app.src.orchestrator import build_orchestrator

    def on_progress(msg: str):
        important = ["Submitting", "✓", "✗", "failed", "Processing", "Done in"]
        if any(k in msg for k in important):
            print(f"  {msg}")

    return build_orchestrator(on_progress=on_progress)


def main():
    parser = argparse.ArgumentParser(
        description="SentinelAI v2 — AI Desktop Assistant"
    )
    parser.add_argument(
        "--voice-only",
        action="store_true",
        help="Run in voice-only console mode (no GUI)",
    )
    parser.add_argument(
        "--no-speak",
        action="store_true",
        help="Disable TTS voice responses",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        default=True,
        help="Launch the full UI (default)",
    )
    args, unknown = parser.parse_known_args()

    if args.voice_only:
        # ── Console / Voice-Only Mode ─────────────────────────────────────────
        print("=" * 60)
        print("  SentinelAI v2 — Voice Mode")
        print("=" * 60)
        print("\n[Boot] Loading orchestrator (all 9 agents)...")
        orch = build_orchestrator_with_progress()
        print("[Boot] Orchestrator ready.\n")

        pipeline = VoiceAgentPipeline(
            orchestrator=orch,
            speak_responses=not args.no_speak,
            on_state_change=lambda s: print(f"  ── {s.upper()} ──"),
            on_answer_ready=lambda a: print(f"\n{'─'*50}\n{a}\n{'─'*50}"),
        )

        try:
            pipeline.run_blocking()
        except KeyboardInterrupt:
            print("\n\nShutting down...")
        return

    # ── UI Mode ───────────────────────────────────────────────────────────────
    try:
        from app.ui.main import launch_ui
        launch_ui()
    except ImportError:
        # Fallback: voice-only if UI not available
        print("[Boot] UI not found — starting in voice-only mode.")
        main_with_voice_only()


def main_with_voice_only():
    """Convenience: voice-only without re-parsing args."""
    print("[Boot] Loading orchestrator...")
    orch = build_orchestrator_with_progress()
    pipeline = VoiceAgentPipeline(orchestrator=orch, speak_responses=True)
    try:
        pipeline.run_blocking()
    except KeyboardInterrupt:
        pipeline.stop()


if __name__ == "__main__":
    main()