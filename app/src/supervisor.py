"""
supervisor.py — Compatibility shim for SentinelAI v2

`rag.py` imports `build_supervisor_system` from this module.
The actual implementation lives in `orchestrator.py`.
"""

from app.src.orchestrator import build_supervisor_system  # noqa: F401

__all__ = ["build_supervisor_system"]
