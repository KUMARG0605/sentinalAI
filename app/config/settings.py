
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent.resolve()

RESOURCES_DIR = BASE_DIR / "resources"


PIPER_EXE = RESOURCES_DIR / "piper_models" / "piper.exe"
PIPER_MODEL = RESOURCES_DIR / "piper_models" / "models" / "toony_cartoon" / "epoch-1104.onnx"
PIPER_MODEL_TELUGU = RESOURCES_DIR / "piper_models" / "models" / "telugu" / "te_IN-venkatesh-medium.onnx"

LLAMA_MODEL = RESOURCES_DIR / "models" / "Llama-3.2-3B-Instruct-Q4_K_M.gguf"

VOSK_MODEL = RESOURCES_DIR / "vosk_models" / "vosk-model-en-in-0.5"


EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_MODEL_LOCAL = RESOURCES_DIR / "models" / "all-MiniLM-L6-v2"


FAISS_INDEX_PATH = RESOURCES_DIR / "faiss_index"


DATA_FOLDER = RESOURCES_DIR / "data"


PIPER_REPO = RESOURCES_DIR / "piper"
PIPER_PATCHES = RESOURCES_DIR / "piper_patches"



LLM_CONFIG = {
    "n_ctx": 4096,
    "n_threads": 8,
    "n_batch": 256,
    "temperature": 0.1,
}



TTS_CONFIG = {
    "words_per_chunk": 4,
    "sample_rate": 22050,
    "sentence_silence": 0.25,
}

STT_CONFIG = {
    "sample_rate": 16000,
}

EMBEDDING_CONFIG = {
    "device": "cpu",
    "batch_size": 32,
    "chunk_size": 1000,
    "chunk_overlap": 200,
}
