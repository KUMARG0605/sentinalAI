
import os
import re
import time
import wave
import queue
import signal
import threading
import subprocess
import tempfile
import concurrent.futures
from typing import List

import numpy as np
import sounddevice as sd
import requests
import dotenv

from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.llms import LlamaCpp
from langchain_community.chat_models import ChatOpenAI

dotenv.load_dotenv()

from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent.parent
RESOURCES_DIR = PROJECT_ROOT / "resources"

PIPER_EXE = str(RESOURCES_DIR / "piper_models" / "piper.exe")
PIPER_MODEL = str(RESOURCES_DIR / "piper_models" / "models" / "toony_cartoon" / "epoch-1104.onnx")
PIPER_MODEL_TELUGU = str(RESOURCES_DIR / "piper_models" / "models" / "telugu" / "te_IN-venkatesh-medium.onnx")
LLAMA_MODEL = str(RESOURCES_DIR / "models" / "Llama-3.2-3B-Instruct-Q4_K_M.gguf")
EMBEDDING_MODEL = str(RESOURCES_DIR / "models" / "all-MiniLM-L6-v2")
FAISS_INDEX_PATH = str(RESOURCES_DIR / "faiss_index")

SAMPLE_RATE = 22050

text_queue = queue.Queue()
audio_queue = queue.Queue()

server_proc = None



def normalize_for_tts(text: str) -> str:
    """Normalize text for TTS output."""
    text = re.sub(r"[*_#`~<>]", "", text)
    text = re.sub(r"[!?]{2,}", ".", text)
    text = re.sub(r"\s+[!?:;,]\s*", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def should_flush(text: str) -> bool:
    """Determine if text buffer should be flushed to TTS."""
    text = text.strip()
    if re.search(r"[.!?]$", text):
        return True
    if len(text.split()) >= 30:
        return True
    return False



def audio_worker():
    """Worker thread for playing audio files."""
    start_time = time.time()
    first_audio = True
    
    while True:
        wav_path = audio_queue.get()
        if wav_path is None:
            audio_queue.task_done()
            break

        with wave.open(wav_path, "rb") as wf:
            audio = wf.readframes(wf.getnframes())
            audio = np.frombuffer(audio, dtype=np.int16)
            sd.play(audio, wf.getframerate())
            
            if first_audio:
                first_audio = False
                elapsed = time.time() - start_time
                print(f"[Audio] Time to first audio: {elapsed:.2f}s")
            
            sd.wait()

        os.remove(wav_path)
        audio_queue.task_done()


def tts_worker(model_path: str = None):
    """Worker thread for text-to-speech conversion."""
    model = model_path or PIPER_MODEL_TELUGU
    
    while True:
        text = text_queue.get()
        if text is None:
            text_queue.task_done()
            audio_queue.put(None)
            break

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
            wav_path = f.name

        subprocess.run(
            [
                PIPER_EXE,
                "--model", model,
                "--sentence_silence", "0.2",
                "--length_scale", "1.05",
                "--output_file", wav_path
            ],
            input=text,
            text=True,
            encoding='utf-8',
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        audio_queue.put(wav_path)
        text_queue.task_done()


def llm_worker(llm, prompt: str):
    """Worker thread for LLM inference with streaming."""
    buffer = ""
    start_time = time.time()
    first_token = True
    
    print("=" * 50)
    print("PROMPT:")
    print(prompt)
    print("=" * 50)
    
    for out in llm.stream(prompt):
        token = out.content
        print(token, end="", flush=True)
        
        if first_token:
            first_token = False
            elapsed = time.time() - start_time
            print(f"\n[LLM] Time to first token: {elapsed:.2f}s")

        buffer += token
        stripped = buffer.strip()
        
        if stripped in {".", "!", "?", ",", ";", ":"}:
            continue

        if should_flush(stripped):
            clean = normalize_for_tts(stripped)
            if clean:
                text_queue.put(clean)
            buffer = ""

    if buffer.strip():
        clean = normalize_for_tts(buffer.strip())
        if clean:
            text_queue.put(clean)
    
    text_queue.put(None)

def build_prompt_telugu(question: str, docs: List[Document]) -> str:
    """Build a Telugu response prompt."""
    context_blocks = []

    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page", "unknown")
        context_blocks.append(
            f"[SOURCE {i}]\n"
            f"File: {source}\n"
            f"Page Number: {page}\n"
            f"Content:\n{doc.page_content}\n"
        )

    context = "\n\n".join(context_blocks)

    prompt = f"""
You are a friendly Telugu-speaking assistant. Write your ENTIRE response in Telugu script only (తెలుగు లిపి మాత్రమే).

CRITICAL LANGUAGE REQUIREMENT:
- Write EVERYTHING in Telugu script (తెలుగు లిపి) - NO English letters allowed
- For English words, TRANSLITERATE them into Telugu script
- Mix Telugu words and transliterated English words naturally

Answer the question ONLY using the provided sources.
If not in sources, say "సారీ, నా దగ్గర ఎనఫ్ ఇన్ఫర్మేషన్ లేదు బట్ నా నాలెడ్జ్ ప్రకారం..." and explain.

FORMATTING:
- Flowing narrative for TTS audio
- NO numbered lists (1., 2., 3.)
- Natural transitions
- Conversational and natural

QUESTION:
{question}

SOURCES:
{context}

మీ ఆన్సర్ ఇక్కడ రాయండి (only in Telugu script):
"""
    return prompt.strip()


def build_prompt_english(question: str, docs: List[Document]) -> str:

    context_blocks = []

    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page", "unknown")
        context_blocks.append(
            f"[SOURCE {i}]\n"
            f"File: {source}\n"
            f"Page Number: {page}\n"
            f"Content:\n{doc.page_content}\n"
        )

    context = "\n\n".join(context_blocks)

    prompt = f"""
You are Sentinel, a witty and friendly personal assistant.

TASK:
Explain the topic ONLY using the provided sources. If not there, say "Hmm, I'm not seeing that in your files, but from what I know..." and explain.

FORMATTING:
- Flowing narrative ONLY
- NO NUMBERED LISTS. NO BULLET POINTS.
- Use transitions like "To start things off,", "Moving right along,", "And get this,"
- Write numbers as words (e.g., "seven" instead of "7")

QUESTION:
{question}

SOURCES:
{context}

ANSWER:
"""
    return prompt.strip()
def load_embeddings():
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"local_files_only": True, "device": "cpu"},
        encode_kwargs={"batch_size": 32},
    )


def load_llm_local():
    cpu_cores = os.cpu_count() // 2 if os.cpu_count() else 4
    print(f"Using {cpu_cores} CPU threads for LLM inference")
    
    return LlamaCpp(
        model_path=LLAMA_MODEL,
        n_ctx=2048,
        n_threads=cpu_cores,
        n_batch=512,
        f16_kv=False,
        use_mlock=True,
        use_mmap=True,
    )


def start_llama_server(server_exe: str = None,model_path: str = None,n_ctx: int = 4096,n_gpu_layers: int = 35,host: str = "127.0.0.1",port: int = 8080):
    if server_exe is None:
        server_exe = "C:/Users/nchar/Downloads/llama-b7531-bin-win-cuda-12.4-x64/llama-server.exe"
    if model_path is None:
        model_path = LLAMA_MODEL
    
    cmd = [
        server_exe,
        "-m", model_path,
        "-c", str(n_ctx),
        "-ngl", str(n_gpu_layers),
        "--host", host,
        "--port", str(port),
    ]

    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


def wait_for_llama_server(url: str = "http://127.0.0.1:8080/health", timeout: int = 60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(url, timeout=1)
            if r.status_code == 200:
                return
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError("llama-server did not become ready")


def stop_llama_server(proc):

    if proc and proc.poll() is None:
        proc.send_signal(signal.CTRL_BREAK_EVENT)
        proc.wait(timeout=10)


def load_essentials():
    global server_proc
    
    start_time = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        embedding_future = executor.submit(load_embeddings)
        llm_future = executor.submit(start_llama_server)
        
        embeddings = embedding_future.result()
        server_proc = llm_future.result()
    
    elapsed = time.time() - start_time
    print(f"[Load] Embeddings + LLM server started in {elapsed:.2f}s")
    
    start_time = time.time()
    wait_for_llama_server()
    elapsed = time.time() - start_time
    print(f"[Load] LLM server ready in {elapsed:.2f}s")
    
    db = FAISS.load_local(
        FAISS_INDEX_PATH,
        embeddings,
        allow_dangerous_deserialization=True,
    )

    retriever = db.as_retriever(search_kwargs={"k": 3})
    
    llm = ChatOpenAI(
        base_url="http://127.0.0.1:8080/v1",
        api_key="not-needed",
        temperature=0.2,
        max_tokens=512,
    )

    return retriever, llm, server_proc


def ask(question: str, retriever, llm, language: str = "telugu"):
    docs = retriever.invoke(question)
    
    if language == "telugu":
        prompt = build_prompt_telugu(question, docs)
    else:
        prompt = build_prompt_english(question, docs)
    
    # Start worker threads
    audio_thread = threading.Thread(target=audio_worker)
    tts_thread = threading.Thread(target=tts_worker)
    llm_thread = threading.Thread(target=llm_worker, args=(llm, prompt))

    audio_thread.start()
    tts_thread.start()
    llm_thread.start()

    llm_thread.join()
    text_queue.join()
    audio_queue.join()

    tts_thread.join()
    audio_thread.join()

    print("\n" + "=" * 80)
    print("CITATIONS:\n")

    for i, doc in enumerate(docs, 1):
        print(f"[SOURCE {i}]")
        print(f"File: {doc.metadata.get('source')}")
        print(f"Type: {doc.metadata.get('type', 'unknown')}")
        print(f"Page: {doc.metadata.get('page', 'unknown')}")
        print("-" * 80)
        print(doc.page_content)
        print()


if __name__ == "__main__":
    retriever, llm, server_proc = load_essentials()
    try:
        ask("explain the hierarchial clustering", retriever, llm)
    finally:
        stop_llama_server(server_proc)
        print("\nLlama server stopped successfully.")
