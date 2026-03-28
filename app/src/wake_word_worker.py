import sys
import json
import queue
import time
from pathlib import Path

import webrtcvad
import sounddevice as sd
from vosk import Model, KaldiRecognizer

# Ensure project root is importable even when this file is launched as a script.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.src.path_utils import project_root
from app.src.stt import DEFAULT_MODEL_PATH, SAMPLE_RATE, Frame, vad_collector
from app.src.config import wake_word

PROJECT_ROOT = project_root()

def _resolve_wake_words() -> set[str]:
    base = wake_word() or "sentinel"
    base = base.strip().lower()
    words = set()
    if base:
        words.update({base, f"hey {base}", f"hello {base}"})
    else:
        words.update({"sentinel", "hey sentinel", "hello sentinel"})
    return {w for w in words if w}

def run_wake_word_detector(model_path: str = None, on_wake=None, cmd_queue=None):
    if model_path is None:
        # Always resolve fresh — never use the module-level constant which was
        # baked at import time before app_state.json was loaded in frozen builds.
        from app.src.stt import resolve_vosk_model_path
        model_path = resolve_vosk_model_path()
    if not model_path or not Path(model_path).is_dir():
        print(
            "[WAKE-PY] ERROR: Vosk wake-word model directory not found. "
            f"Expected at: {model_path}",
            file=sys.stderr,
        )
        return 2
        
    print(f"[WAKE-PY] Loading Vosk model from {model_path}...", file=sys.stderr)
    model = Model(model_path)
    rec = KaldiRecognizer(model, SAMPLE_RATE)
    rec.SetWords(True)
    
    # Very relaxed VAD for wake word (we want to catch everything)
    vad = webrtcvad.Vad(1) 
    
    frame_duration_ms = 30
    padding_duration_ms = 500  # short padding for fast wake response
    
    q = queue.Queue()

    def callback(indata, frames, time_info, status):
        q.put(bytes(indata))

    block_size = int(SAMPLE_RATE * frame_duration_ms / 1000) # 480 samples at 16kHz
    
    print("[WAKE-PY] Listening for wake words...", file=sys.stderr)
    
    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=block_size, dtype="int16", channels=1, callback=callback):
        print("[WAKE-PY] Listening for wake words...", file=sys.stderr)
        
        while True:
            # Continuously process audio frames as they arrive
            # This avoids slicing words across hard 2-second boundaries
            data = q.get()
            
            # Feed continuously to Vosk
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                text = res.get("text", "").lower()
            else:
                res = json.loads(rec.PartialResult())
                text = res.get("partial", "").lower()

            if not text:
                continue
            
            # Check if any wake word appears in the current context
            found_wake = False
            wake_keywords = _resolve_wake_words()
            for wake in wake_keywords:
                if wake in text:
                    found_wake = True
                    break
                    
            if found_wake:
                print(f"[WAKE-PY] Wake word detected: '{text}'", file=sys.stderr)
                
                # Signal main pipeline
                if on_wake:
                    on_wake()
                else:
                    print("WAKE")
                    sys.stdout.flush()
                
                # Wait for RESUME signal
                while True:
                    if cmd_queue:
                        line = cmd_queue.get()
                    else:
                        line = sys.stdin.readline()
                    if not line:
                        return 0
                    line = line.strip().upper()
                    if line == "RESUME":
                        print("[WAKE-PY] Resuming...", file=sys.stderr)
                        # Clear backlog and reset recognizer
                        while not q.empty():
                            try:
                                q.get_nowait()
                            except queue.Empty:
                                break
                        rec.Reset()
                        break
                    elif line == "EXIT":
                        return 0
    return 0
    return 0

if __name__ == "__main__":
    try:
        sys.exit(run_wake_word_detector())
    except KeyboardInterrupt:
        sys.exit(0)
