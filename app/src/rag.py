"""
rag.py — SentinelAI brain

Entry points (unchanged API):
    load_essentials()  →  (None, llm, None)
    ask(question, retriever, llm, language, abort_event)  →  {"answer": ..., ...}

The single ReAct agent is REPLACED with a Supervisor + 6 Subagent system:

    User query
        └─▶  SUPERVISOR  (routes + synthesises)
                 ├─▶  SystemAgent   — open apps, click, type, windows
                 ├─▶  FileAgent     — files, CMD, shell
                 ├─▶  WebAgent      — websites, search
                 ├─▶  MediaAgent    — music, video
                 ├─▶  RAGAgent      — FAISS knowledge base
                 └─▶  UtilityAgent  — date, screenshot, clipboard, ask user
"""

import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import wave

import dotenv
import numpy as np
import sounddevice as sd
from langchain.agents import AgentExecutor
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from app.src.llm_rotation import get_llm, get_default_model, is_rate_limited

from app.src.config import (
    assistant_language,
    embedding_model_path,
    piper_executable_path,
    piper_primary_model_path,
    piper_secondary_model_path,
    faiss_index_path,
)
from app.src.tools import set_retriever, set_ask_user_handler
from app.src.supervisor import build_supervisor_system

dotenv.load_dotenv()

# ── TTS queues ────────────────────────────────────────────────────────────────
text_queue  = queue.Queue()
audio_queue = queue.Queue()

# ── Module-level state ────────────────────────────────────────────────────────
_SUPERVISOR:          AgentExecutor | None = None
_RETRIEVER                                 = None
_LLM                                       = None
_ABORT_EVENT:  threading.Event | None      = None
_SAMBANOVA_KEYS:      list[str]            = []
_CURRENT_KEY_INDEX:   int                  = 0


# ═════════════════════════════════════════════════════════════════════════════
#  TTS / AUDIO PIPELINE  (unchanged)
# ═════════════════════════════════════════════════════════════════════════════

def normalize_for_tts(text: str) -> str:
    text = re.sub(r"[*_#`~<>]", "", text)
    text = re.sub(r"[!?]{2,}", ".", text)
    text = re.sub(r"\s+[!?:;,]\s*", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _drain_queue(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
            q.task_done()
        except queue.Empty:
            break


def audio_worker(abort_event: threading.Event | None):
    while True:
        if abort_event and abort_event.is_set():
            break
        try:
            wav_path = audio_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if wav_path is None:
            audio_queue.task_done()
            break
        if abort_event and abort_event.is_set():
            audio_queue.task_done()
            break
        with wave.open(wav_path, "rb") as wf:
            audio = wf.readframes(wf.getnframes())
            audio = np.frombuffer(audio, dtype=np.int16)
            sd.play(audio, wf.getframerate())
            sd.wait()
        try:
            os.remove(wav_path)
        except OSError:
            pass
        audio_queue.task_done()


def tts_worker(model_path: str | None = None, piper_exe: str | None = None,
               abort_event: threading.Event | None = None):
    model     = model_path or piper_secondary_model_path()
    piper_cmd = piper_exe  or piper_executable_path()
    while True:
        if abort_event and abort_event.is_set():
            break
        try:
            text = text_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if text is None:
            text_queue.task_done()
            audio_queue.put(None)
            break
        if abort_event and abort_event.is_set():
            text_queue.task_done()
            break
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
            wav_path = f.name
        result = subprocess.run(
            [piper_cmd, "--model", model,
             "--sentence_silence", "0.2",
             "--length_scale", "1.05",
             "--output_file", wav_path],
            input=text, text=True, encoding="utf-8", capture_output=True,
        )
        if result.returncode != 0 or not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            try:
                os.remove(wav_path)
            except OSError:
                pass
            text_queue.task_done()
            continue
        audio_queue.put(wav_path)
        text_queue.task_done()


# ═════════════════════════════════════════════════════════════════════════════
#  EMBEDDINGS  (unchanged)
# ═════════════════════════════════════════════════════════════════════════════

def load_embeddings():
    embed_path = embedding_model_path()
    local_only = True
    if not os.path.exists(embed_path):
        embed_path = "sentence-transformers/all-MiniLM-L6-v2"
        local_only = False

    def _make(model_name: str, local: bool):
        kwargs = {"device": "cpu"}
        if local:
            try:
                from sentence_transformers import SentenceTransformer
                import inspect
                if "local_files_only" in inspect.signature(SentenceTransformer.__init__).parameters:
                    kwargs["local_files_only"] = True
            except Exception:
                pass
        return HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs=kwargs,
            encode_kwargs={"batch_size": 32},
        )

    try:
        return _make(embed_path, local_only)
    except Exception as exc:
        print(f"[WARN] Embeddings local load failed: {exc}. Trying hub…")
        try:
            return _make("sentence-transformers/all-MiniLM-L6-v2", False)
        except Exception as e2:
            print(f"[WARN] Embeddings unavailable — RAG disabled: {e2}")
            return None


# ═════════════════════════════════════════════════════════════════════════════
#  SAMBANOVA KEY MANAGEMENT  (unchanged)
# ═════════════════════════════════════════════════════════════════════════════

def _load_sambanova_keys() -> list[str]:
    keys: list[str] = []
    primary = os.getenv("SAMBANOVA_API_KEY", "").strip()
    if primary:
        keys.append(primary)
    for i in range(1, 11):
        v = os.getenv(f"sambanova{i}", "").strip()
        if v:
            keys.append(v)
    deduped, seen = [], set()
    for k in keys:
        if k not in seen:
            seen.add(k)
            deduped.append(k)
    return deduped


def _build_sambanova_llm():
    return get_llm(model=get_default_model(), temperature=0.2)

def _set_active_key(index: int) -> None:
    global _CURRENT_KEY_INDEX
    if not _SAMBANOVA_KEYS:
        raise RuntimeError("No SambaNova keys loaded.")
    _CURRENT_KEY_INDEX = index % len(_SAMBANOVA_KEYS)
    os.environ["SAMBANOVA_API_KEY"] = _SAMBANOVA_KEYS[_CURRENT_KEY_INDEX]


def _is_rate_limited(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(x in msg for x in (
        "rate limit", "too many requests", "status code: 429", "http 429", "quota"
    ))


def _rotate_key_and_rebuild() -> bool:
    """Rotate to the next API key and rebuild the entire Supervisor + subagents."""
    global _SUPERVISOR, _CURRENT_KEY_INDEX
    if len(_SAMBANOVA_KEYS) <= 1:
        return False
    _set_active_key((_CURRENT_KEY_INDEX + 1) % len(_SAMBANOVA_KEYS))
    print(f"[SambaNova] Rotated to key {_CURRENT_KEY_INDEX + 1}/{len(_SAMBANOVA_KEYS)}")
    try:
        new_llm    = _build_sambanova_llm()
        _SUPERVISOR = build_supervisor_system(new_llm, abort_event=_ABORT_EVENT)
        set_retriever(_RETRIEVER)
        return True
    except Exception as exc:
        print(f"[WARN] Rebuild after key rotation failed: {exc}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
#  LAZY RETRIEVER  (unchanged)
# ═════════════════════════════════════════════════════════════════════════════

def _ensure_retriever_loaded():
    global _RETRIEVER
    if _RETRIEVER is not None:
        return
    print("[RAG] Loading FAISS index on first query…")
    start      = time.time()
    embeddings = load_embeddings()
    if embeddings is not None:
        try:
            db         = FAISS.load_local(
                str(faiss_index_path()), embeddings,
                allow_dangerous_deserialization=True,
            )
            _RETRIEVER = db.as_retriever(search_kwargs={"k": 3})
            set_retriever(_RETRIEVER)
            print(f"[RAG] FAISS ready in {time.time() - start:.2f}s")
        except Exception as exc:
            print(f"[WARN] FAISS load failed — RAG disabled: {exc}")
            set_retriever(None)
    else:
        print("[WARN] Embeddings unavailable — RAG disabled.")
        set_retriever(None)


# ═════════════════════════════════════════════════════════════════════════════
#  LOAD ESSENTIALS
# ═════════════════════════════════════════════════════════════════════════════

def load_essentials():
    """
    Build the Supervisor + all 6 Subagents.
    FAISS is still deferred to the first ask().

    Returns (None, llm, None) — SAME SIGNATURE as old single-agent version.
    conversation_ui.py and workers.py need ZERO changes.
    """
    global _SUPERVISOR, _RETRIEVER, _LLM
    start           = time.time()

    _RETRIEVER = None
    set_retriever(None)

    llm  = _build_sambanova_llm()
    _LLM = llm

    # ── THIS is the change: build Supervisor instead of single AgentExecutor ─
    _SUPERVISOR = build_supervisor_system(llm, abort_event=None)

    print(f"[Load] Supervisor + 6 subagents ready in {time.time() - start:.2f}s  "
          f"(FAISS deferred to first query)")

    return None, llm, None


# ═════════════════════════════════════════════════════════════════════════════
#  ASK  — main entry point called by conversation_ui / workers
# ═════════════════════════════════════════════════════════════════════════════

def ask(question: str, retriever, llm,
        language: str | None = None,
        abort_event: threading.Event | None = None):
    """
    Route the user query through Supervisor → Subagents, speak the answer.
    PUBLIC SIGNATURE IS IDENTICAL to the old single-agent ask().
    """
    global _SUPERVISOR, _RETRIEVER, _ABORT_EVENT

    _ABORT_EVENT = abort_event          # stored so key-rotation rebuilds carry it
    _ensure_retriever_loaded()
    set_retriever(_RETRIEVER)

    if _SUPERVISOR is None:
        _SUPERVISOR = build_supervisor_system(llm or _LLM, abort_event=abort_event)

    lang = (language or assistant_language()).strip().lower()

    _drain_queue(text_queue)
    _drain_queue(audio_queue)

    # ── Invoke Supervisor with 429-retry key rotation ─────────────────────────
    start        = time.time()
    result       = None
    from app.src.llm_rotation import _GROQ_KEYS, _SAMBANOVA_KEYS as _SN_KEYS, _detect_provider
    max_attempts = max(1, len(_GROQ_KEYS if _detect_provider() == "groq" else _SN_KEYS))

    for attempt in range(1, max_attempts + 1):
        if abort_event and abort_event.is_set():
            return {"answer": "", "sources": [], "language": lang}
        try:
            result = _SUPERVISOR.invoke({"input": question})
            break
        except Exception as exc:
            if _is_rate_limited(exc) and attempt < max_attempts:
                from app.src.llm_rotation import _detect_provider
                print(f"[{_detect_provider().upper()}] Rate limit attempt {attempt}/{max_attempts} — rotating key…")
                if _rotate_key_and_rebuild():
                    set_retriever(_RETRIEVER)
                    continue
            raise

    print(f"[Supervisor] Done in {time.time() - start:.2f}s")

    answer = normalize_for_tts(str(result.get("output", "")).strip())

    # ── TTS ───────────────────────────────────────────────────────────────────
    tts_model = (piper_secondary_model_path()
                 if lang.startswith("tel")
                 else piper_primary_model_path())

    audio_t = threading.Thread(target=audio_worker, args=(abort_event,))
    tts_t   = threading.Thread(
        target=tts_worker,
        kwargs={"model_path": tts_model,
                "piper_exe":  piper_executable_path(),
                "abort_event": abort_event},
    )
    audio_t.start()
    tts_t.start()

    if answer:
        text_queue.put(answer)
    text_queue.put(None)

    if not (abort_event and abort_event.is_set()):
        text_queue.join()
        audio_queue.join()

    tts_t.join()
    audio_t.join()

    return {"answer": answer, "sources": [], "language": lang}


# ── Compat stub ───────────────────────────────────────────────────────────────
def stop_llama_server(proc):
    return None


# ── Quick smoke test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    _, llm, _ = load_essentials()
    try:
        prompt="Create a Python file on the desktop called greet.py with a script that prints Hello Sentinel, then open CMD and run it"
        out = ask(prompt, retriever=None, llm=llm)
        print(out)
    finally:
        stop_llama_server(None)