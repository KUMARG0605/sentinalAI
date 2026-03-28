"""
session_memory.py — Persistent Session Memory for SentinelAI v2

Stores two kinds of memory:
  1. Task log      — every prompt the user gave + each agent's input/output
  2. Agent history — per-agent rolling conversation (last N turns)
                     passed as `chat_history` so agents remember prior context

Storage: ~/.sentinel/memory.json  (created automatically)

Structure:
  {
    "tasks": [
      {
        "id": "abc12345",
        "timestamp": "2024-01-15T10:30:00",
        "prompt": "find ML jobs on naukri",
        "agent_runs": [
          {
            "agent":    "browser_agent",
            "input":    "search for machine learning jobs...",
            "output":   "Found 12 jobs: ...",
            "success":  true,
            "error":    "",
            "duration": 15.3
          }
        ],
        "final_answer": "Found 12 ML jobs on Naukri...",
        "success": true
      }
    ],
    "agent_history": {
      "ecommerce_agent": [
        {"role": "user",      "content": "order keyboard from flipkart"},
        {"role": "assistant", "content": "Found 5 keyboards under ₹2000..."}
      ]
    }
  }
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, BaseMessage


# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

MEMORY_DIR  = Path(os.path.expanduser("~")) / ".sentinel"
MEMORY_FILE = MEMORY_DIR / "memory.json"

MAX_TASKS           = 50   # keep last 50 tasks in log
MAX_HISTORY_TURNS   = 10   # keep last 10 turns per agent (user+assistant = 2 messages each)
MAX_HISTORY_CHARS   = 800  # truncate very long messages


# ─────────────────────────────────────────────────────────────────────────────
#  DATA MODEL
# ─────────────────────────────────────────────────────────────────────────────

class AgentRun:
    """Single agent execution record."""
    __slots__ = ("agent", "input", "output", "success", "error", "duration", "attempt")

    def __init__(
        self,
        agent: str,
        input: str,
        output: str = "",
        success: bool = True,
        error: str = "",
        duration: float = 0.0,
        attempt: int = 1,
    ):
        self.agent    = agent
        self.input    = input
        self.output   = output
        self.success  = success
        self.error    = error
        self.duration = duration
        self.attempt  = attempt

    def to_dict(self) -> dict:
        return {
            "agent":    self.agent,
            "input":    self.input[:600],
            "output":   self.output[:600],
            "success":  self.success,
            "error":    self.error[:300],
            "duration": round(self.duration, 2),
            "attempt":  self.attempt,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentRun":
        return cls(
            agent=d.get("agent", ""),
            input=d.get("input", ""),
            output=d.get("output", ""),
            success=d.get("success", True),
            error=d.get("error", ""),
            duration=d.get("duration", 0.0),
            attempt=d.get("attempt", 1),
        )


class TaskRecord:
    """Complete record of one user task."""
    def __init__(
        self,
        task_id: str,
        prompt: str,
        agent_runs: Optional[list[AgentRun]] = None,
        final_answer: str = "",
        success: bool = True,
        timestamp: Optional[str] = None,
    ):
        self.task_id      = task_id
        self.prompt       = prompt
        self.agent_runs   = agent_runs or []
        self.final_answer = final_answer
        self.success      = success
        self.timestamp    = timestamp or datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "id":           self.task_id,
            "timestamp":    self.timestamp,
            "prompt":       self.prompt[:300],
            "agent_runs":   [r.to_dict() for r in self.agent_runs],
            "final_answer": self.final_answer[:400],
            "success":      self.success,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskRecord":
        return cls(
            task_id=d.get("id", uuid.uuid4().hex[:8]),
            prompt=d.get("prompt", ""),
            agent_runs=[AgentRun.from_dict(r) for r in d.get("agent_runs", [])],
            final_answer=d.get("final_answer", ""),
            success=d.get("success", True),
            timestamp=d.get("timestamp", ""),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  SESSION MEMORY
# ─────────────────────────────────────────────────────────────────────────────

class SessionMemory:
    """
    Thread-safe persistent memory.

    Usage:
        mem = SessionMemory()
        tid = mem.start_task("order keyboard from flipkart")
        mem.record_agent_run(tid, AgentRun("ecommerce_agent", input, output, True))
        mem.finish_task(tid, "Found 3 keyboards...", success=True)

        # Get chat history for a specific agent
        history = mem.get_chat_history("ecommerce_agent")  # list[BaseMessage]
    """

    def __init__(self, path: Path = MEMORY_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._tasks: list[TaskRecord] = []
        self._agent_history: dict[str, list[dict]] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._tasks = [TaskRecord.from_dict(t) for t in data.get("tasks", [])]
                self._agent_history = data.get("agent_history", {})
        except Exception as e:
            print(f"[SessionMemory] Load failed (starting fresh): {e}")
            self._tasks = []
            self._agent_history = {}

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "tasks":         [t.to_dict() for t in self._tasks[-MAX_TASKS:]],
                "agent_history": self._agent_history,
            }
            self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[SessionMemory] Save failed: {e}")

    # ── Task lifecycle ────────────────────────────────────────────────────────

    def start_task(self, prompt: str) -> str:
        """Register a new task. Returns task_id."""
        task_id = uuid.uuid4().hex[:8]
        with self._lock:
            self._tasks.append(TaskRecord(task_id=task_id, prompt=prompt))
        return task_id

    def record_agent_run(self, task_id: str, run: AgentRun):
        """Store the result of one agent execution for a task."""
        with self._lock:
            for t in reversed(self._tasks):
                if t.task_id == task_id:
                    t.agent_runs.append(run)
                    break

            # Update per-agent rolling chat history
            agent = run.agent
            if agent not in self._agent_history:
                self._agent_history[agent] = []

            hist = self._agent_history[agent]

            # Add user turn (what was sent to the agent)
            user_text = run.input[:MAX_HISTORY_CHARS]
            hist.append({"role": "user", "content": user_text})

            # Add assistant turn (what the agent returned)
            if run.success and run.output:
                ai_text = run.output[:MAX_HISTORY_CHARS]
                hist.append({"role": "assistant", "content": ai_text})
            elif run.error:
                hist.append({
                    "role": "assistant",
                    "content": f"[FAILED after {run.attempt} attempt(s): {run.error[:200]}]"
                })

            # Keep only the last MAX_HISTORY_TURNS * 2 messages
            max_msgs = MAX_HISTORY_TURNS * 2
            if len(hist) > max_msgs:
                self._agent_history[agent] = hist[-max_msgs:]

            self._save()

    def finish_task(self, task_id: str, final_answer: str, success: bool):
        """Mark a task as complete with its final answer."""
        with self._lock:
            for t in reversed(self._tasks):
                if t.task_id == task_id:
                    t.final_answer = final_answer[:400]
                    t.success = success
                    break
            self._save()

    # ── Chat history for agents ───────────────────────────────────────────────

    def get_chat_history(self, agent_name: str) -> list[BaseMessage]:
        """
        Return the last N turns of conversation for the given agent
        as a list of LangChain BaseMessage objects.

        Pass this directly as `chat_history` when invoking an agent:
            agent.invoke({"input": ..., "chat_history": mem.get_chat_history("ecommerce_agent")})
        """
        with self._lock:
            hist = self._agent_history.get(agent_name, [])

        messages: list[BaseMessage] = []
        for msg in hist:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
        return messages

    # ── Supervisor context ────────────────────────────────────────────────────

    def get_supervisor_context(self, max_tasks: int = 5) -> str:
        """
        Return a summary of recent tasks for the supervisor/orchestrator.
        Includes agent outputs so follow-up prompts can reference prior results
        (e.g. file paths found, URLs opened, search results).
        """
        with self._lock:
            recent = self._tasks[-max_tasks:]

        if not recent:
            return "No previous tasks."

        lines = ["RECENT TASK HISTORY:"]
        for t in recent:
            status = "✓" if t.success else "✗"
            lines.append(f"\n{status} [{t.timestamp[:16]}] {t.prompt[:80]}")
            for run in t.agent_runs:
                run_status = "OK" if run.success else f"FAILED: {run.error[:60]}"
                lines.append(f"   {run.agent}: {run_status}")
                # Include output so follow-ups can resolve paths / results
                if run.success and run.output:
                    lines.append(f"   Output: {run.output[:200]}")
            if t.final_answer:
                lines.append(f"   Answer: {t.final_answer[:120]}")

        return "\n".join(lines)

    def get_failed_task_context(self, task_id: str) -> str:
        """
        Return full context of a failed task so the orchestrator can retry intelligently.
        """
        with self._lock:
            for t in self._tasks:
                if t.task_id == task_id:
                    lines = [
                        f"RETRY CONTEXT for task {task_id}:",
                        f"Original prompt: {t.prompt}",
                        f"",
                        "What was attempted:",
                    ]
                    for run in t.agent_runs:
                        status = "SUCCEEDED" if run.success else f"FAILED (attempt {run.attempt})"
                        lines.append(f"  [{run.agent}] {status}")
                        if run.success:
                            lines.append(f"    Output: {run.output[:200]}")
                        else:
                            lines.append(f"    Error:  {run.error[:200]}")
                            lines.append(f"    Input:  {run.input[:200]}")
                    return "\n".join(lines)
        return f"No context found for task {task_id}"

    # ── Recent memory summary ─────────────────────────────────────────────────

    def get_recent_summary(self, n: int = 3) -> str:
        """Short summary of the last n completed tasks. Used for user-facing 'what did I do last?'"""
        with self._lock:
            recent = [t for t in self._tasks if t.final_answer][-n:]

        if not recent:
            return "No completed tasks yet."

        lines = []
        for t in recent:
            lines.append(f"• {t.timestamp[:16]}: {t.prompt[:60]} → {t.final_answer[:80]}")
        return "\n".join(lines)


# ── Module-level singleton ────────────────────────────────────────────────────

_memory: Optional[SessionMemory] = None
_memory_lock = threading.Lock()


def get_memory() -> SessionMemory:
    global _memory
    with _memory_lock:
        if _memory is None:
            _memory = SessionMemory()
    return _memory
