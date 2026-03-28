"""
scheduler.py — Parallel Task Scheduler with Memory & Retry
"""

from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Optional

from app.src.blackboard import Blackboard
from app.src.dag_builder import Task, TaskDAG
from app.src.session_memory import SessionMemory, AgentRun, get_memory


class TaskResult:
    def __init__(self, task_id, agent, output="", error="", duration=0.0, attempts=1):
        self.task_id  = task_id
        self.agent    = agent
        self.output   = output
        self.error    = error
        self.duration = duration
        self.success  = not bool(error)
        self.attempts = attempts

    def __repr__(self):
        status = "OK" if self.success else f"ERROR: {self.error[:60]}"
        return f"TaskResult({self.task_id}, {self.agent}, {status}, {self.duration:.1f}s)"


class TaskScheduler:
    MAX_WORKERS = 6
    MAX_RETRIES = 2

    # Hard failures — retrying will NEVER fix these
    _NO_RETRY_PATTERNS = (
        "tool call validation failed",
        "attempted to call tool",    # LLM tried a tool it doesn't own
        "which was not in request",  # same — wrong tool for this agent
        "parameters for tool",
        "did not match",
        "invalid_request_error",
        "apistatuserror",
        "context_length_exceeded",
        "maximum context length",
        "authentication",
        "unauthorized",
        "never set (timeout)",
    )


    def __init__(self, agent_registry, blackboard, abort_event=None,
                 session_memory=None, task_id=""):
        self.agents         = agent_registry
        self.bb             = blackboard
        self.abort_event    = abort_event or threading.Event()
        self.memory         = session_memory or get_memory()
        self.parent_task_id = task_id
        self.verify_tools: dict = {}
        self._completed: set[str] = set()
        self._failed: set[str]    = set()
        self._lock  = threading.Lock()
        self._results: list[TaskResult] = []

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, dag, on_progress=None):
        log = on_progress or print
        log(f"[Scheduler] Starting {len(dag.tasks)} tasks...")

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            pending: dict[Future, Task] = {}
            submitted: set[str] = set()
            self._submit_ready(dag, pool, pending, submitted, log)

            while pending:
                if self.abort_event.is_set():
                    log("[Scheduler] Aborted.")
                    break

                done = [f for f in list(pending.keys()) if f.done()]
                if not done:
                    time.sleep(0.2)
                    continue

                for future in done:
                    task = pending.pop(future)
                    try:
                        result: TaskResult = future.result()
                        self._results.append(result)
                        with self._lock:
                            if result.success:
                                self._completed.add(task.id)
                                log(f"[Scheduler] ✓ {task.id} ({task.agent}) "
                                    f"done in {result.duration:.1f}s "
                                    f"(attempt {result.attempts})")
                            else:
                                self._failed.add(task.id)
                                log(f"[Scheduler] ✗ {task.id} ({task.agent}) "
                                    f"failed after {result.attempts} attempt(s): "
                                    f"{result.error[:120]}")
                                self._cascade_fail(dag, task.id, log)
                    except Exception as exc:
                        log(f"[Scheduler] Exception in {task.id}: {exc}")
                        with self._lock:
                            self._failed.add(task.id)

                    self._submit_ready(dag, pool, pending, submitted, log)

        log(f"[Scheduler] Done. Completed: {len(self._completed)}, "
            f"Failed: {len(self._failed)}")
        return self._results

    # ── Task submission ───────────────────────────────────────────────────────

    def _submit_ready(self, dag, pool, pending, submitted, log):
        with self._lock:
            completed_snap = set(self._completed)
            failed_snap    = set(self._failed)

        for task in dag.tasks:
            if task.id in submitted:
                continue
            if any(dep in failed_snap for dep in task.depends_on):
                with self._lock:
                    self._failed.add(task.id)
                submitted.add(task.id)
                log(f"[Scheduler] ↷ {task.id} skipped (dependency failed)")
                continue
            if all(dep in completed_snap for dep in task.depends_on):
                submitted.add(task.id)
                self.bb.set_task_status(task.id, "running")
                log(f"[Scheduler] → Submitting {task.id} ({task.agent}): "
                    f"{task.instruction[:60]}")
                future = pool.submit(self._run_task_with_retry, task)
                pending[future] = task

    # ── Error classification ──────────────────────────────────────────────────

    @classmethod
    def _is_rate_limit(cls, error: str) -> bool:
        e = error.lower()
        return any(x in e for x in ("rate limit", "rate_limit", "429",
                                     "too many requests", "quota exceeded"))

    @classmethod
    def _is_retryable(cls, error: str) -> bool:
        e = error.lower()
        if cls._is_rate_limit(e):
            return True
        return not any(pat in e for pat in cls._NO_RETRY_PATTERNS)

    # ── Retry wrapper ─────────────────────────────────────────────────────────

    def _run_task_with_retry(self, task: Task) -> TaskResult:
        last_error = ""
        total_start = time.time()

        for attempt in range(1, self.MAX_RETRIES + 2):
            if self.abort_event.is_set():
                return TaskResult(task.id, task.agent, error="Aborted")

            result = self._run_task(task, attempt=attempt, prev_error=last_error)
            if result.success:
                return result

            last_error = result.error

            if not self._is_retryable(last_error):
                print(f"[Scheduler] ✗ {task.id} non-retryable on attempt {attempt}: "
                      f"{last_error[:160]}")
                result.duration = time.time() - total_start
                result.attempts = attempt
                return result

            if attempt <= self.MAX_RETRIES:
                wait = 5.0 * attempt if self._is_rate_limit(last_error) else 1.5 * attempt
                label = "rate-limit backoff" if self._is_rate_limit(last_error) else "retry"
                print(f"[Scheduler] ⟳ {task.id} attempt {attempt} — {label} "
                      f"({wait:.0f}s): {last_error[:80]}")
                time.sleep(wait)
            else:
                result.duration = time.time() - total_start
                result.attempts = attempt
                return result

        return TaskResult(task.id, task.agent, error=last_error,
                          duration=time.time() - total_start,
                          attempts=self.MAX_RETRIES + 1)

    # ── Single attempt ────────────────────────────────────────────────────────

    def _run_task(self, task: Task, attempt: int = 1, prev_error: str = "") -> TaskResult:
        start = time.time()
        self.bb.set_task_status(task.id, "running")

        try:
            # Wait for blackboard keys from upstream dependencies
            for key in task.reads:
                if not task.depends_on:
                    if not self.bb.has(key):
                        print(f"[Scheduler] ℹ {task.id}: skipping read '{key}' "
                              f"(no upstream dependency, key not in blackboard)")
                        continue
                else:
                    try:
                        self.bb.wait_for(key, timeout=120.0)
                    except TimeoutError:
                        return TaskResult(task.id, task.agent,
                                          error=f"Blackboard key '{key}' never set (timeout)",
                                          duration=time.time() - start, attempts=attempt)

            instruction = self._enrich_instruction(task, prev_error=prev_error, attempt=attempt)

            agent = self.agents.get(task.agent)
            if agent is None:
                return TaskResult(task.id, task.agent,
                                  error=f"Agent '{task.agent}' not registered",
                                  duration=time.time() - start, attempts=attempt)

            raw = agent.invoke({
                "input": instruction,
                "chat_history": self.memory.get_chat_history(task.agent),
            })

            output = (str(raw.get("output", "")).strip()
                      if isinstance(raw, dict) else str(raw).strip())


            # ── Success path ──────────────────────────────────────────────────
            for key in task.writes:
                self.bb.set(key, output)
            self.bb.set(f"task_output_{task.id}", output)
            self.bb.set_task_status(task.id, "done", result=output)

            duration = time.time() - start
            self.memory.record_agent_run(
                self.parent_task_id,
                AgentRun(agent=task.agent, input=instruction[:600],
                         output=output[:600], success=True,
                         duration=duration, attempt=attempt),
            )
            return TaskResult(task.id, task.agent, output=output,
                              duration=duration, attempts=attempt)

        except Exception as exc:
            duration = time.time() - start
            err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-400:]}"
            self.bb.set_task_status(task.id, "failed", error=err)
            self.memory.record_agent_run(
                self.parent_task_id,
                AgentRun(agent=task.agent, input=task.instruction[:600],
                         output="", success=False, error=err[:300],
                         duration=duration, attempt=attempt),
            )
            return TaskResult(task.id, task.agent, error=err,
                              duration=duration, attempts=attempt)

    # ── Instruction builder ───────────────────────────────────────────────────

    def _enrich_instruction(self, task: Task, prev_error: str = "",
                             attempt: int = 1) -> str:
        """
        Builds the full prompt for one agent invocation:
          1. Tool-use header (action agents only)
          2. Base task instruction
          3. Upstream blackboard data
          4. Retry context (attempts > 1)
          5. Recent session memory (attempt 1 only)
        """
        parts = [task.instruction]

        # Upstream data from blackboard
        for key in task.reads:
            val = self.bb.get(key)
            if val is not None:
                val_str = str(val)
                if len(val_str) > 1200:
                    val_str = val_str[:1200] + "...[truncated]"
                parts.append(f"\n[DATA FROM PREVIOUS STEP — use as input]:\n{val_str}")

        # Retry context — strip internal error prefixes before showing to LLM
        if attempt > 1 and prev_error:
            clean = (prev_error
                     .replace("NO_TOOL_CALLED:", "")
                     .replace("HALLUCINATION_DETECTED:", "")
                     .strip()[:300])
            parts.append(
                f"\n[RETRY — attempt {attempt}]:\n"
                f"Previous attempt failed: {clean}\n"
                f"Correct the above and retry using only YOUR available tools."
            )

        # Session memory (first attempt only)
        if attempt == 1:
            recent = self.memory.get_supervisor_context(max_tasks=2)
            if recent and "No previous tasks" not in recent:
                parts.append(f"\n[RECENT SESSION CONTEXT]:\n{recent}")

        return "\n".join(parts)

    # ── Cascade failure ───────────────────────────────────────────────────────

    def _cascade_fail(self, dag: TaskDAG, failed_id: str, log: Callable):
        with self._lock:
            for task in dag.tasks:
                if (failed_id in task.depends_on
                        and task.id not in self._completed
                        and task.id not in self._failed):
                    self._failed.add(task.id)
                    log(f"[Scheduler] ↷ {task.id} cascaded-failed (upstream: {failed_id})")
                    self._cascade_fail(dag, task.id, log)