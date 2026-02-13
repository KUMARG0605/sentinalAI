"""SentinelAI - Local AI Assistant with RAG, TTS, and STT capabilities."""

from . import indexer
from . import rag
from . import stt

__version__ = "1.0.0"
__all__ = ["indexer", "rag", "stt"]
