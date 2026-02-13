import json
import queue
import sys
import collections
import contextlib
import sounddevice as sd
from vosk import Model, KaldiRecognizer
import os
import wave
import webrtcvad
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
RESOURCES_DIR = PROJECT_ROOT / "resources"
DEFAULT_MODEL_PATH = str(RESOURCES_DIR / "vosk_models" / "vosk-model-en-in-0.5")
SAMPLE_RATE = 16000

class Frame(object):
    """Represents a "frame" of audio data."""
    def __init__(self, bytes, timestamp, duration):
        self.bytes = bytes
        self.timestamp = timestamp
        self.duration = duration

def frame_generator(frame_duration_ms, audio, sample_rate):
    """Generates audio frames from PCM audio data."""
    n = int(sample_rate * (frame_duration_ms / 1000.0) * 2)
    offset = 0
    timestamp = 0.0
    duration = (float(n) / sample_rate) / 2.0
    while offset + n < len(audio):
        yield Frame(audio[offset:offset + n], timestamp, duration)
        timestamp += duration
        offset += n

def vad_collector(sample_rate, frame_duration_ms, padding_duration_ms, vad, frames):
    """Filters out non-voiced audio frames."""
    num_padding_frames = int(padding_duration_ms / frame_duration_ms)
    ring_buffer = collections.deque(maxlen=num_padding_frames)
    triggered = False

    voiced_frames = []
    
    for frame in frames:
        is_speech = vad.is_speech(frame.bytes, sample_rate)

        if not triggered:
            ring_buffer.append((frame, is_speech))
            num_voiced = len([f for f, speech in ring_buffer if speech])
            if num_voiced > 0.9 * ring_buffer.maxlen:
                triggered = True
                print(">> Started listening...")
                for f, s in ring_buffer:
                    voiced_frames.append(f)
                ring_buffer.clear()
        else:
            voiced_frames.append(frame)
            ring_buffer.append((frame, is_speech))
            num_unvoiced = len([f for f, speech in ring_buffer if not speech])
            if num_unvoiced > 0.9 * ring_buffer.maxlen:
                triggered = False
                print(">> Stopped listening (Silence detected).")
                yield b''.join([f.bytes for f in voiced_frames])
                ring_buffer.clear()
                voiced_frames = []

def transcribe_mic_vad(model_path: str = None) -> str:
    """Transcribes audio from microphone using VAD for endpointing."""
    if model_path is None:
        model_path = DEFAULT_MODEL_PATH
        
    model = Model(model_path)
    rec = KaldiRecognizer(model, SAMPLE_RATE)
    rec.SetWords(True)
    
    vad = webrtcvad.Vad(3) # Aggressiveness mode 3
    
    frame_duration_ms = 30
    padding_duration_ms = 1000 # 1 sec of silence to stop
    chunk_size = int(SAMPLE_RATE * frame_duration_ms / 1000) # 480 samples

    q = queue.Queue()

    def callback(indata, frames, time_info, status):
        q.put(bytes(indata))

    print("---------------- VAD Listening... (Speak now) ---------------------------\n")
    
    # We need to collect raw audio and feed it to VAD
    # To simplify, we'll read from queue and yield frames
    def audio_frame_generator():
        timestamp = 0.0
        duration = frame_duration_ms / 1000.0
        while True:
            data = q.get()
            # indata is likely larger than our frame size, need to ensure 30ms chunks
            # But creating a buffer wrapper is cleaner.
            yield Frame(data, timestamp, duration)
            timestamp += duration

    # Using RawInputStream with exactly the chunk size we need for VAD
    # 30ms at 16kHz = 480 samples
    block_size = 480 
    
    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=block_size, dtype="int16", channels=1, callback=callback):
        # Create a generator that yields frames from the queue
        def queue_generator():
            timestamp = 0.0
            duration = frame_duration_ms / 1000.0
            while True:
                data = q.get()
                yield Frame(data, timestamp, duration)
                timestamp += duration

        frames = queue_generator()
        
        # Use VAD collector to get voiced chunks
        # This implementation yields one big chunk of audio when speech ends
        for voiced_audio in vad_collector(SAMPLE_RATE, frame_duration_ms, padding_duration_ms, vad, frames):
            if rec.AcceptWaveform(voiced_audio):
                result = json.loads(rec.Result())
                text = result.get("text", "")
                if text:
                    print(f"Detected: {text}")
                    return text
            else:
                 # Check final result if VAD stops but AcceptWaveform didn't trigger
                 # This might happen for short phrases
                 final = json.loads(rec.Result()) # Use Result() to not reset, or FinalResult() ?
                 # Ideally we process the whole chunk.
                 pass
            
            # If we got here, silence was detected and we processed the chunk.
            # Check for final result from the recognizer
            final = json.loads(rec.FinalResult())
            text = final.get("text", "")
            if text:
                print(f"Detected (Final): {text}")
                return text
            
            # If nothing detected, we might want to loop back (return to listening)
            # But the user wants "stop listening and stt", so we return whatever we have.
            # If it's empty, we might return empty string and let pipeline handle loop.
            return ""

    return ""

# Keep original for compatibility if needed, or redirect
def transcribe_mic(model_path: str = None) -> str:
    return transcribe_mic_vad(model_path)


def transcribe_file(audio_path: str, model_path: str = None) -> str:    
    if model_path is None:
        model_path = DEFAULT_MODEL_PATH
    
    model = Model(model_path)
    rec = KaldiRecognizer(model, SAMPLE_RATE)
    rec.SetWords(True)
    
    with wave.open(audio_path, "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != SAMPLE_RATE:
            print(f"Warning: Audio should be WAV, mono, 16-bit, {SAMPLE_RATE}Hz")
        
        results = []
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                if result.get("text"):
                    results.append(result["text"])
        
        final = json.loads(rec.FinalResult())
        if final.get("text"):
            results.append(final["text"])
        
        return " ".join(results)


if __name__ == "__main__":
    result_text = transcribe_mic()
    print("---------------- Final Output ---------------------------")
    print(result_text)
