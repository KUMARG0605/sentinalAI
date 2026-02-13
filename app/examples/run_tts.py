import sys
import os
import threading
import queue
import tempfile
import subprocess
import wave
import re
from pathlib import Path

import numpy as np
import sounddevice as sd
from llama_cpp import Llama
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

RESOURCES_DIR = PROJECT_ROOT / "resources"
PIPER_EXE = str(RESOURCES_DIR / "piper_models" / "piper.exe")
PIPER_MODEL = str(RESOURCES_DIR / "piper_models" / "en_US-lessac" / "en-us-lessac-low.onnx")
LLAMA_MODEL = str(RESOURCES_DIR / "models" / "Llama-3.2-3B-Instruct-Q4_K_M.gguf")

SAMPLE_RATE = 22050
text_queue = queue.Queue()
audio_queue = queue.Queue()


def normalize_for_tts(text: str) -> str:
    """Clean text for TTS."""
    text = re.sub(r"[*_#`~<>]", "", text)
    text = re.sub(r"[!?]{2,}", ".", text)
    text = re.sub(r"\s+[!?:;,]\s*", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def should_flush(text: str) -> bool:
    """Check if buffer should be flushed."""
    text = text.strip()
    if re.search(r"[.!?]$", text):
        return True
    if len(text.split()) >= 15:
        return True
    return False


def audio_worker():
    """Play audio files from queue."""
    while True:
        wav_path = audio_queue.get()
        if wav_path is None:
            audio_queue.task_done()
            break

        with wave.open(wav_path, "rb") as wf:
            audio = wf.readframes(wf.getnframes())
            audio = np.frombuffer(audio, dtype=np.int16)
            sd.play(audio, wf.getframerate())
            sd.wait()

        os.remove(wav_path)
        audio_queue.task_done()


def tts_worker():
    """Convert text to speech."""
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
                "--model", PIPER_MODEL,
                "--sentence_silence", "0.25",
                "--output_file", wav_path
            ],
            input=text,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        audio_queue.put(wav_path)
        text_queue.task_done()


def llm_worker(prompt):
    """Generate LLM response with streaming."""
    llm = Llama(
        model_path=LLAMA_MODEL,
        n_ctx=2048,
        n_threads=12,
        n_batch=1024,
        verbose=False
    )

    buffer = ""

    for out in llm(
        prompt,
        stream=True,
        max_tokens=512,
        temperature=0.7,
        top_p=0.9,
    ):
        token = out["choices"][0]["text"]
        print(token, end="", flush=True)

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


def speak(prompt: str):
    """Generate and speak a response to the prompt."""
    print("\n--- STARTING ASYNC PIPELINE ---\n")

    audio_thread = threading.Thread(target=audio_worker)
    tts_thread = threading.Thread(target=tts_worker)
    llm_thread = threading.Thread(target=llm_worker, args=(prompt,))

    audio_thread.start()
    tts_thread.start()
    llm_thread.start()

    llm_thread.join()
    text_queue.join()
    audio_queue.join()

    tts_thread.join()
    audio_thread.join()

    print("\n\n--- DONE ---")


def main():
    speak("Explain photosynthesis in very simple terms.")


if __name__ == "__main__":
    main()
