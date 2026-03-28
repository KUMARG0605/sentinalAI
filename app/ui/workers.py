"""
workers.py — QThread Workers for SentinelAI v2

IndexerWorker       — FAISS indexer (unchanged)
AssistantWorker     — wake word loop using Orchestrator + checkpointer
IndexStatusWorker   — checks indexer status (unchanged)
"""

import contextlib
import io
import threading

from PyQt5.QtCore import QThread, pyqtSignal

from app.src.index_runtime import clear_index_stop
from app.ui.state import (
    release_assistant_lock,
    release_index_lock,
    try_acquire_assistant_lock,
    try_acquire_index_lock,
)
from app.src.stt import transcribe_mic
from app.src.voice_pipeline import (
    is_conversation_ending,
    resume_wake_word,
    speak_text,
    start_wake_word_process,
    stop_wake_word,
    wait_for_wake_word,
)


# ─────────────────────────────────────────────────────────────────────────────
#  INDEXER WORKER  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class IndexerWorker(QThread):
    log       = pyqtSignal(str)
    progress  = pyqtSignal(dict)
    completed = pyqtSignal(dict)
    failed    = pyqtSignal(str)

    def __init__(self, root_folder: str, index_path: str, exclude_paths: list[str]):
        super().__init__()
        self.root_folder   = root_folder
        self.index_path    = index_path
        self.exclude_paths = exclude_paths
        self._stop_event   = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        stream = _QtLogStream(self.log.emit)
        clear_index_stop()
        print(f"[INDEXER] root={self.root_folder}  index={self.index_path}")
        if not try_acquire_index_lock():
            self.log.emit("Indexer already running — skipped.")
            self.completed.emit({"status": "already_running"})
            return
        try:
            from app.src.indexer import create_index
            with contextlib.redirect_stdout(stream):
                result = create_index(
                    data_folder=self.root_folder,
                    index_path=self.index_path,
                    exclude_paths=self.exclude_paths,
                    max_workers=1,
                    batch_size=1,
                    stop_requested=self._stop_event.is_set,
                    progress_callback=self.progress.emit,
                )
            self.completed.emit(result or {"status": "unknown"})
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.failed.emit(str(exc))
        finally:
            release_index_lock()


# ─────────────────────────────────────────────────────────────────────────────
#  ASSISTANT WORKER  — Orchestrator + Checkpointer (replaces old RAG ask())
# ─────────────────────────────────────────────────────────────────────────────

class AssistantWorker(QThread):
    """
    Wake word detection loop that passes speech transcripts to the
    new multi-agent Orchestrator instead of the single-agent RAG system.

    Each wake event starts a new conversation thread_id for the checkpointer
    so state is isolated per session but persists across app restarts.
    """
    status               = pyqtSignal(str)
    transcript           = pyqtSignal(str)
    wave_mode            = pyqtSignal(str)
    conversation_started = pyqtSignal()
    conversation_ended   = pyqtSignal()
    failed               = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._running          = True
        self._end_conversation = threading.Event()
        self._orchestrator     = None

    def stop(self) -> None:
        self._running = False
        self._end_conversation.set()

    def end_conversation_now(self) -> None:
        self._end_conversation.set()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_orchestrator(self):
        if self._orchestrator is not None:
            return self._orchestrator
        self.status.emit("Loading AI models…")
        try:
            from app.src.orchestrator import build_orchestrator
            self._orchestrator = build_orchestrator()
            self.status.emit("AI ready.")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.failed.emit(f"Orchestrator load failed: {e}")
        return self._orchestrator

    def _new_thread_id(self) -> str:
        try:
            from app.src.checkpointer import new_thread_id
            return new_thread_id()
        except Exception:
            import uuid
            return f"assistant-{uuid.uuid4().hex[:10]}"

    def _run_orchestrator(self, prompt: str, thread_id: str) -> str:
        orch = self._orchestrator
        if orch is None:
            return "Sorry, the AI system could not be loaded."
        try:
            if hasattr(orch, "memory") and hasattr(orch.memory, "_thread_id"):
                orch.memory._thread_id = thread_id
            result = orch.run(prompt)
            return result.get("answer", "") if isinstance(result, dict) else str(result)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"Sorry, I encountered an error: {e}"

    # ── Main thread ───────────────────────────────────────────────────────────

    def run(self) -> None:
        wake_proc = None
        if not try_acquire_assistant_lock():
            self.status.emit("Assistant already running in another process.")
            return

        try:
            print("[ASSISTANT] Starting wake word + orchestrator loop…")
            self.status.emit("Starting wake word detector…")
            self.wave_mode.emit("idle")

            wake_proc = start_wake_word_process()
            self.status.emit("Listening for wake word.")
            self.wave_mode.emit("wake")

            while self._running:
                wait_for_wake_word(wake_proc)
                if not self._running:
                    break

                print("[ASSISTANT] Wake word detected!")
                self._end_conversation.clear()
                self.conversation_started.emit()
                self.status.emit("Wake word detected!")
                self.wave_mode.emit("wake")

                # Fresh conversation = new checkpointer thread
                thread_id = self._new_thread_id()
                greeted   = False

                while self._running and not self._end_conversation.is_set():

                    if not greeted:
                        self.wave_mode.emit("talk")
                        speak_text("How can I help you?")
                        greeted = True

                    self.status.emit("Listening…")
                    self.wave_mode.emit("listen")
                    self.transcript.emit("")

                    query = transcribe_mic()
                    if not query:
                        continue

                    print(f"[ASSISTANT] Heard: {query}")
                    self.transcript.emit(f"You: {query}")

                    if is_conversation_ending(query):
                        self.wave_mode.emit("talk")
                        speak_text("Alright. Going back to wake mode.")
                        break

                    self.status.emit("Thinking…")
                    self.wave_mode.emit("talk")

                    # Lazy-load orchestrator on first real query
                    orch = self._load_orchestrator()
                    if orch is None:
                        speak_text("Sorry, I could not load the AI system.")
                        break

                    answer = self._run_orchestrator(query, thread_id)

                    if answer:
                        self.transcript.emit(f"Assistant: {answer}")
                        if not self._end_conversation.is_set():
                            first_sentence = answer[:300].split(".")[0].strip()
                            speak_text(first_sentence or answer[:200])

                    if not self._end_conversation.is_set():
                        speak_text("Anything else?")

                self.conversation_ended.emit()
                resume_wake_word(wake_proc)
                self.status.emit("Listening for wake word.")
                self.wave_mode.emit("wake")

        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.failed.emit(str(exc))
        finally:
            if wake_proc:
                stop_wake_word(wake_proc)
            release_assistant_lock()


# ─────────────────────────────────────────────────────────────────────────────
#  INDEX STATUS WORKER  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class IndexStatusWorker(QThread):
    log       = pyqtSignal(str)
    completed = pyqtSignal(dict)
    failed    = pyqtSignal(str)

    def __init__(self, root_folder: str, index_path: str, exclude_paths: list[str]):
        super().__init__()
        self.root_folder   = root_folder
        self.index_path    = index_path
        self.exclude_paths = exclude_paths

    def run(self) -> None:
        stream = _QtLogStream(self.log.emit)
        try:
            from app.src.indexer import get_index_status
            with contextlib.redirect_stdout(stream):
                result = get_index_status(
                    data_folder=self.root_folder,
                    index_path=self.index_path,
                    exclude_paths=self.exclude_paths,
                )
            self.completed.emit(result or {"status": "unknown"})
        except Exception as exc:
            self.failed.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────

class _QtLogStream(io.StringIO):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def write(self, s):
        if s and s.strip():
            self._callback(s.rstrip())
        return len(s)


# ─────────────────────────────────────────────────────────────────────────────
#  TEXT PROMPT WORKER
# ─────────────────────────────────────────────────────────────────────────────

import time

class TextPromptWorker(QThread):
    started_processing = pyqtSignal(str)
    progress_update    = pyqtSignal(str)
    response_ready     = pyqtSignal(str, list)
    status_changed     = pyqtSignal(str)
    hitl_question      = pyqtSignal(dict)
    failed             = pyqtSignal(str)

    _hitl_answers: dict = {}

    # ── Singleton orchestrator — shared across ALL TextPromptWorker instances ──
    # This is the key to conversation continuity: one orchestrator lives for the
    # entire app session, so SessionMemory, blackboard context, and agent history
    # accumulate across turns instead of resetting on every message.
    _shared_orchestrator = None
    _shared_orch_lock    = threading.Lock()

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    @classmethod
    def preload(cls):
        """Warm-start the shared orchestrator in a background thread."""
        def _load():
            try:
                cls._get_or_create_orchestrator()
            except Exception:
                pass
        threading.Thread(target=_load, daemon=True).start()

    @classmethod
    def answer_hitl(cls, question_id: str, answer: str):
        cls._hitl_answers[question_id] = answer

    @classmethod
    def _get_or_create_orchestrator(cls):
        """
        Return the shared Orchestrator, creating it once if needed.
        progress/hitl callbacks are instance-level — we patch them per-run.
        """
        with cls._shared_orch_lock:
            if cls._shared_orchestrator is None:
                from app.src.orchestrator import build_orchestrator
                cls._shared_orchestrator = build_orchestrator()
            return cls._shared_orchestrator

    def run(self) -> None:
        self.started_processing.emit(self.query)
        self.status_changed.emit("Thinking…")
        try:
            orch = self._get_or_create_orchestrator()

            # Patch per-run callbacks onto the shared instance
            orch.on_progress      = self.progress_update.emit
            orch.on_hitl_question = self._handle_hitl

            result = orch.run(self.query)

            ans = result.get("answer", "")
            self.response_ready.emit(ans, [])
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.failed.emit(str(e))

    def _handle_hitl(self, question: dict):
        self.hitl_question.emit(question)
        qid = question.get("id")
        while qid not in self._hitl_answers:
            time.sleep(0.5)
        question["answer"] = self._hitl_answers.pop(qid)
