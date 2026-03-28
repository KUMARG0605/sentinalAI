"""
orchestrator.py — SentinelAI v2 Orchestrator

Combines the best of both architectures:
  • v1 supervisor.py  → CHECK-ACT loop, verification tools (screenshot + file check)
  • v2 architecture   → DAG-based parallel execution, Blackboard, 8 specialist agents

FLOW FOR ANY PROMPT:
  1. DAGBuilder         → decompose prompt into parallel/sequential tasks
  2. FilterExtractor    → parse structured filters (if ordering/searching)
  3. TaskScheduler      → run tasks in parallel via thread pool
  4. CHECK-ACT loop     → after each agent call: verify result, retry if failed
  5. HITL gate          → pause for user input when needed (product list, seats, payment)
  6. Response assembly  → combine all task outputs into final answer

CHECK-ACT LOOP (from v1 supervisor):
  ACT   → delegate task to specialist agent
  CHECK → verify_with_screenshot() or verify_file_exists()
  DECIDE → pass: next task | fail: retry (max 2) | give up: report error
"""

from __future__ import annotations

import os

import threading
import time
import uuid
from typing import Callable, Optional

from app.src.llm_rotation import get_llm, get_default_model

from app.src.blackboard import Blackboard
from app.src.dag_builder import DAGBuilder, TaskDAG
from app.src.scheduler import TaskScheduler, TaskResult
from app.src.filter_extractor import FilterExtractor
from app.src.session_memory import SessionMemory, get_memory

# All agents
from app.src.agents.research_agent import build_research_agent
from app.src.agents.browser_agent import build_browser_agent
from app.src.agents.ecommerce_agent import build_ecommerce_agent
from app.src.agents.comms_agent import build_comms_agent
from app.src.agents.agents import (
    build_system_agent,
    build_file_agent,
    build_media_agent,
    build_rag_agent,
    build_utility_agent,
    build_terminal_agent,       # NEW: shell/PowerShell/pip/git
    build_code_agent,           # NEW: code writing/editing/running
    build_verification_tools,   # from supervisor.py CHECK-ACT
)
# ── Improved system agent with screenshot verification & visual navigation ──
from app.src.agents.system_agent_v2 import build_system_agent_v2


# ─────────────────────────────────────────────────────────────────────────────
#  ORCHESTRATOR SYSTEM PROMPT  (extends supervisor.py prompt)
# ─────────────────────────────────────────────────────────────────────────────

_ORCHESTRATOR_PROMPT = """You are Sentinel, an AI desktop assistant for Windows.

You coordinate 8 specialist agents using a CHECK-ACT loop:
  → Delegate ONE task to ONE agent
  → Verify the result (screenshot or file check)
  → Decide next step based on actual outcome
  → Repeat until done

══════════════════════════════════════════════════════
YOUR AGENTS — ROUTING IS MANDATORY, NO EXCEPTIONS
══════════════════════════════════════════════════════
system_agent    — open/close apps, click, type, focus windows, install apps
file_agent      — create/read/write/delete/search files, CSV, Excel, PDF, image conversion
terminal_agent  — run PowerShell/CMD commands, execute Python scripts, pip install,
                  git operations, create venvs, open terminal windows (NEW)
code_agent      — write code from scratch, edit/patch files, scaffold projects,
                  run and debug code iteratively (NEW)
media_agent     — find and play local music/video, open YouTube
rag_agent       — search local FAISS knowledge base
utility_agent   — date/time, ask user, clipboard, hotkeys, notifications
research_agent  — web search (DuckDuckGo/Tavily), YouTube search, Wikipedia, ArXiv
                  NO browser needed — pure API/HTTP searches

browser_agent   — GENERAL web browsing only. Sites: naukri, linkedin, indeed,
                  internshala, github, wikipedia, news sites, any non-shopping site.
                  Use for: job search, data scraping, form filling (non-payment),
                  reading articles, extracting content from websites.

ecommerce_agent — ALL SHOPPING AND BOOKING. No exceptions.
                  ┌─────────────────────────────────────────────────────┐
                  │ TRIGGER WORDS → ecommerce_agent (NEVER browser)    │
                  │                                                     │
                  │  Sites: flipkart, amazon, myntra, meesho, ajio,   │
                  │         swiggy, zomato, blinkit, bigbasket,        │
                  │         irctc, makemytrip, easemytrip, redbus,     │
                  │         bookmyshow, paytm, snapdeal, nykaa         │
                  │                                                     │
                  │  Actions: buy, order, purchase, shop, add to cart, │
                  │           book ticket, book movie, book train,      │
                  │           find cheapest product, compare prices,    │
                  │           search on flipkart/amazon, product list   │
                  └─────────────────────────────────────────────────────┘
                  Stops before payment. Never clicks Pay/Confirm Order.

══════════════════════════════════════════════════════
ROUTING DECISION TABLE — follow exactly
══════════════════════════════════════════════════════

  "search for jobs on naukri"              → browser_agent
  "find ML jobs in Bangalore"              → browser_agent
  "scrape github trending"                 → browser_agent
  "search the web for AI news"             → research_agent (no browser needed)
  "search youtube for tutorials"           → research_agent (no browser needed)
  "what is the weather in Mumbai"          → research_agent (no browser needed)

  "search flipkart for keyboards"          → ecommerce_agent  ← NOT browser_agent
  "find cheapest keyboard on amazon"       → ecommerce_agent  ← NOT browser_agent
  "go to flipkart and search keyboards"    → ecommerce_agent  ← NOT browser_agent
  "show me keyboards under 2000 on amazon" → ecommerce_agent  ← NOT browser_agent
  "order pizza from swiggy"               → ecommerce_agent
  "book movie ticket on bookmyshow"        → ecommerce_agent
  "book train on irctc"                   → ecommerce_agent
  "find running shoes on myntra"           → ecommerce_agent  ← NOT browser_agent

  KEY RULE: If the site is a SHOPPING or DELIVERY or BOOKING site → ecommerce_agent.
  Even if the action is just "search" or "find" or "show me" — if it's on
  Flipkart / Amazon / Myntra / Swiggy / Zomato / IRCTC / BookMyShow → ecommerce_agent.

VERIFICATION TOOLS (use after EVERY agent call):
  verify_with_screenshot(question)    — screenshot + yes/no question to vision LLM
  verify_file_exists(path, snippet)   — check file exists with expected content

══════════════════════════════════════════════════════
THE CHECK-ACT LOOP (MANDATORY)
══════════════════════════════════════════════════════

For EVERY sub-task:

  STEP 1 — ACT: delegate ONE specific task to ONE agent.
            Be specific: full paths, exact text, window titles, button names.

  STEP 2 — CHECK: immediately verify:
            • After app open / UI action  → verify_with_screenshot("Is [app] visible?")
            • After file write            → verify_file_exists("C:/path/file.txt", "snippet")
            • After browser action        → verify_with_screenshot("Is [page/element] visible?")
            • After typing in window      → verify_with_screenshot("Does window show [text]?")

  STEP 3 — DECIDE:
            • CHECK passed → proceed to next sub-task
            • CHECK failed → retry with corrected approach (max 2 retries)
            • Still failing → report failure to user and stop

══════════════════════════════════════════════════════
FILE WRITING RULE
══════════════════════════════════════════════════════
NEVER write files by opening Notepad. CORRECT pattern:
  ACT   → file_agent("create C:/Users/nchar/Desktop/list.txt with: milk\\neggs")
  CHECK → verify_file_exists("C:/Users/nchar/Desktop/list.txt", "milk")
  ACT   → system_agent("open C:/Users/nchar/Desktop/list.txt in Notepad")
  CHECK → verify_with_screenshot("Is Notepad open showing list.txt?")

══════════════════════════════════════════════════════
BROWSER TASK PATTERNS — correct agent for each
══════════════════════════════════════════════════════
For JOB SEARCH / SCRAPING (browser_agent):
  ACT   → browser_agent("search naukri.com for ML engineer jobs in Bangalore, extract listings")
  CHECK → verify_file_exists or verify_with_screenshot

For SHOPPING / ORDERING / BOOKING (ecommerce_agent):
  ACT   → ecommerce_agent("search flipkart for mechanical keyboards under 2000, show product list")
  CHECK → verify_with_screenshot("Is the Flipkart product list visible?")
  ← NEVER use browser_agent here, even if the word "search" appears in the task

For WEB RESEARCH (research_agent — no browser needed):
  ACT   → research_agent("search web for average full stack salary Hyderabad")
  CHECK → no screenshot needed, use output directly

══════════════════════════════════════════════════════
PARALLEL EXECUTION
══════════════════════════════════════════════════════
For multi-part prompts ("do X AND do Y AND do Z"):
  → Run independent tasks in parallel (no shared data)
  → Run dependent tasks sequentially (B uses A's output)
  → Example: "read PDF, search YouTube, save links, share on Telegram"
    T1: file_agent(read PDF) → T2: research_agent(YouTube) → T3+T4 parallel:
      [file_agent(save notepad), comms_agent(share Telegram)]

══════════════════════════════════════════════════════
RULES
══════════════════════════════════════════════════════
- ALWAYS use C:/Users/nchar as user home (never %USERPROFILE% or ~)
- Give agents COMPLETE instructions: full paths, exact text, button labels
- Ambiguous request? → utility_agent(ask user to clarify)
- NEVER submit payments without explicit user confirmation
- After ALL tasks verified, reply in 1-2 sentences confirming what was done
"""


# ─────────────────────────────────────────────────────────────────────────────
#  ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

class Orchestrator:
    """
    Central orchestrator combining:
      - v1 CHECK-ACT loop with screenshot/file verification
      - v2 parallel DAG execution with shared blackboard
      - 8 specialist agents + 2 verification tools
      - Human-in-the-loop gate for selections
    """

    def __init__(
        self,
        llm=None,
        abort_event: Optional[threading.Event] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        on_hitl_question: Optional[Callable[[dict], None]] = None,
    ):
        if llm is None:
            llm = get_llm(model=get_default_model(), temperature=0.1)

        self.llm = llm
        self.abort_event = abort_event or threading.Event()
        self.on_progress = on_progress or print
        self.on_hitl_question = on_hitl_question

        # ── Load FAISS retriever BEFORE building agents so rag_agent can use it ──
        self._bootstrap_retriever()

        self._log("Building agent pool (8 agents + 2 verification tools)...")
        self.agents = self._build_agents()
        self.verify_tools = build_verification_tools()  # from uploaded supervisor.py
        self._log(f"Ready: {list(self.agents.keys())} + verify_with_screenshot + verify_file_exists")

        self.dag_builder = DAGBuilder(llm=llm)
        self.filter_extractor = FilterExtractor(llm=llm)
        self.memory = get_memory()
        self.blackboard_state = {}
        self._log(f"Session memory loaded: {len(self.memory._tasks)} past tasks")

    def _log(self, msg: str):
        self.on_progress(f"[Orchestrator] {msg}")

    def _bootstrap_retriever(self):
        """
        Load the FAISS index and inject it into tools.py via set_retriever().
        Called once at Orchestrator startup so rag_agent's search_knowledge_base
        tool has a live retriever instead of None.
        """
        from app.src.tools import set_retriever
        from app.src.config import faiss_index_path
        import time as _time

        try:
            from app.src.rag import load_embeddings
            from langchain_community.vectorstores import FAISS

            index_path = str(faiss_index_path())
            import os
            if not os.path.exists(index_path):
                self._log(f"FAISS index not found at {index_path} — RAG disabled.")
                set_retriever(None)
                return

            self._log("Loading FAISS index for rag_agent…")
            t0 = _time.time()
            embeddings = load_embeddings()
            if embeddings is None:
                self._log("Embeddings unavailable — RAG disabled.")
                set_retriever(None)
                return

            db = FAISS.load_local(
                index_path, embeddings,
                allow_dangerous_deserialization=True,
            )
            retriever = db.as_retriever(search_kwargs={"k": 3})
            set_retriever(retriever)
            self._log(f"FAISS ready in {_time.time() - t0:.2f}s — rag_agent enabled.")
        except Exception as exc:
            self._log(f"FAISS load failed — RAG disabled: {exc}")
            set_retriever(None)

    def _build_agents(self) -> dict:
        return {
            "research_agent":  build_research_agent(self.llm),
            "browser_agent":   build_browser_agent(self.llm),
            "ecommerce_agent": build_ecommerce_agent(self.llm),
            "system_agent":    build_system_agent_v2(self.llm),   # screenshot-verified
            "file_agent":      build_file_agent(self.llm),
            "terminal_agent":  build_terminal_agent(self.llm),    # NEW: shell/pip/git
            "code_agent":      build_code_agent(self.llm),        # NEW: code write/run
            "comms_agent":     build_comms_agent(self.llm),
            "media_agent":     build_media_agent(self.llm),
            "rag_agent":       build_rag_agent(self.llm),
            "utility_agent":   build_utility_agent(self.llm),
        }

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self, prompt: str) -> dict:
        """
        Execute a user prompt end-to-end.

        Flow:
          1. Register task in SessionMemory
          2. Build DAG + run scheduler (retry + chat_history per agent)
          3. If tasks failed: build repair DAG from failure context
          4. Save final answer to SessionMemory

        Returns:
            {
                "answer": str,
                "task_results": list[TaskResult],
                "blackboard": dict,
                "dag_summary": str,
                "duration": float,
            }
        """
        start = time.time()
        task_id = uuid.uuid4().hex[:8]
        self._log(f"Processing: {prompt[:80]} (id={task_id})")

        # Register in persistent session memory
        mem_task_id = self.memory.start_task(prompt)

        bb = Blackboard(task_id=task_id)
        # Restore previous blackboard state (cross-prompt persistence)
        bb.update(self.blackboard_state)
        bb.set("original_prompt", prompt)

        # Inject recent history so agents have context
        supervisor_ctx = self.memory.get_supervisor_context(max_tasks=3)
        if "No previous tasks" not in supervisor_ctx:
            bb.set("supervisor_context", supervisor_ctx)
            self._log(f"Supervisor context: {len(supervisor_ctx)} chars from memory")

        # Extract filters for ordering/searching tasks
        if self._needs_filter_extraction(prompt):
            try:
                filters = self.filter_extractor.extract(prompt)
                bb.set("filter_schema", filters.to_prompt_context())
                self._log(f"Filters: {filters.to_prompt_context()}")
            except Exception as e:
                self._log(f"Filter extraction skipped: {e}")

        # Build task DAG — pass conversation context so follow-up prompts
        # (e.g. "open the pdf you just found") resolve correctly.
        self._log("Building task DAG...")
        dag = self.dag_builder.build(
            prompt,
            conversation_context=supervisor_ctx if "No previous tasks" not in supervisor_ctx else "",
        )
        bb.set("dag_summary", dag.summary())
        self._log("DAG: " + dag.summary())

        # Start HITL monitor
        if self.on_hitl_question:
            threading.Thread(
                target=self._hitl_monitor, args=(bb,), daemon=True
            ).start()

        # Run scheduler (includes per-agent retry, chat_history, memory recording)
        scheduler = TaskScheduler(
            agent_registry=self.agents,
            blackboard=bb,
            abort_event=self.abort_event,
            session_memory=self.memory,
            task_id=mem_task_id,
        )
        scheduler.verify_tools = {t.name: t for t in self.verify_tools}
        results = scheduler.run(dag, on_progress=self.on_progress)

        # ── Repair pass: re-run failed tasks with full failure context ─────────
        failed = [r for r in results if not r.success]
        if failed and not self.abort_event.is_set():
            self._log(f"{len(failed)} task(s) failed after retries. Running repair DAG...")
            repair = self._run_repair_dag(prompt, failed, results, bb, mem_task_id)
            if repair:
                result_map = {r.task_id: r for r in results}
                for rr in repair:
                    result_map[rr.task_id] = rr
                results = list(result_map.values())

        answer   = self._assemble_answer(prompt, results, bb)
        duration = time.time() - start
        n_ok     = sum(1 for r in results if r.success)
        n_all    = len(results)
        self._log(f"Done in {duration:.1f}s. {n_ok}/{n_all} tasks succeeded.")

        # Save blackboard state for next prompt
        clean_state = {
            k: v for k, v in bb.all().items()
            if not k.startswith("__task_status_") and not k.startswith("task_output_")
        }
        self.blackboard_state.update(clean_state)

        # Persist to memory
        self.memory.finish_task(mem_task_id, answer, success=(n_ok == n_all))

        return {
            "answer":       answer,
            "task_results": results,
            "blackboard":   bb.all(),
            "dag_summary":  dag.summary(),
            "duration":     duration,
        }

    def _run_repair_dag(
        self,
        original_prompt: str,
        failed_results: list,
        all_results: list,
        bb: Blackboard,
        mem_task_id: str,
    ) -> list[TaskResult]:
        """
        Build a repair prompt that describes exactly what failed and why,
        then re-runs ONLY those tasks via a new DAG.
        The repair instruction carries full context so agents can course-correct.
        """
        succeeded_ctx = "\n".join(
            f"  [{r.agent}] OUTPUT: {r.output[:150]}" for r in all_results if r.success
        ) or "  (nothing completed yet)"

        failed_ctx = "\n".join(
            f"  [{r.agent}] FAILED after {getattr(r, 'attempts', 1)} attempt(s)\n"
            f"    Error: {r.error[:200]}"
            for r in failed_results
        )

        repair_prompt = (
            f"REPAIR — complete the unfinished parts of this request.\n\n"
            f"Original user request: {original_prompt}\n\n"
            f"Already completed successfully:\n{succeeded_ctx}\n\n"
            f"These parts FAILED and need to be redone:\n{failed_ctx}\n\n"
            f"Please retry the failed steps using a corrected approach. "
            f"Use the completed outputs above as context where needed."
        )
        self._log(f"Repair prompt built ({len(repair_prompt)} chars)")

        try:
            repair_dag = self.dag_builder.build(repair_prompt)
            self._log(f"Repair DAG:\n{repair_dag.summary()}")

            repair_scheduler = TaskScheduler(
                agent_registry=self.agents,
                blackboard=bb,
                abort_event=self.abort_event,
                session_memory=self.memory,
                task_id=mem_task_id,
            )
            return repair_scheduler.run(repair_dag, on_progress=self.on_progress)
        except Exception as e:
            self._log(f"Repair DAG error: {e}")
            return []
    def run_simple(self, prompt: str) -> str:
        """
        Simplified single-call interface matching the v1 supervisor interface:
            result = orchestrator.run_simple("open notepad")

        Returns the answer string directly.
        """
        result = self.run(prompt)
        return result.get("answer", "")

    def _needs_filter_extraction(self, prompt: str) -> bool:
        keywords = [
            "order", "buy", "purchase", "search for", "find me",
            "cheapest", "under ₹", "under rs", "colored", "size",
            "book", "filter", "show me", "get me",
        ]
        pl = prompt.lower()
        return any(kw in pl for kw in keywords)

    def _assemble_answer(self, prompt: str, results: list, bb: Blackboard) -> str:
        if not results:
            return "I wasn't able to complete that task."
        outputs = []
        for r in results:
            if r.success and r.output.strip():
                outputs.append(r.output.strip())
        if not outputs:
            errors = [r.error for r in results if r.error]
            return f"Task failed: {errors[0][:200]}" if errors else "Task completed."
        if len(outputs) == 1:
            return outputs[0]
        parts = []
        for r in results:
            if r.success and r.output.strip():
                parts.append(f"[{r.agent}] {r.output.strip()[:400]}")
        return "\n\n".join(parts)

    def _hitl_monitor(self, bb: Blackboard):
        while not self.abort_event.is_set():
            for q in bb.get_pending_questions():
                if not q.get("_notified"):
                    q["_notified"] = True
                    if self.on_hitl_question:
                        self.on_hitl_question(q)
            time.sleep(0.3)

    def answer_hitl(self, bb: Blackboard, question_id: str, answer: str):
        bb.answer_human(question_id, answer)
        self._log(f"HITL answered: {question_id} → {answer}")

    # ── Legacy supervisor interface ───────────────────────────────────────────

    def invoke(self, inputs: dict) -> dict:
        """
        Drop-in replacement for the v1 supervisor AgentExecutor.invoke().
        Usage: result = orchestrator.invoke({"input": "open notepad"})
        """
        prompt = inputs.get("input", "")
        result = self.run(prompt)
        return {"output": result["answer"]}


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def build_orchestrator(
    llm=None,
    abort_event: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[str], None]] = None,
    on_hitl_question: Optional[Callable[[dict], None]] = None,
) -> Orchestrator:
    """
    Build and return the Orchestrator.
    Drop-in replacement for build_supervisor_system() from v1.

    Usage (same as v1):
        orch = build_orchestrator(llm=llm, abort_event=stop_event)
        result = orch.invoke({"input": "open notepad"})
        # OR
        result = orch.run("order pizza from Swiggy")
        print(result["answer"])
    """
    return Orchestrator(
        llm=llm,
        abort_event=abort_event,
        on_progress=on_progress,
        on_hitl_question=on_hitl_question,
    )


# ── v1 compatibility alias ────────────────────────────────────────────────────
def build_supervisor_system(llm, abort_event=None) -> Orchestrator:
    """
    Backward-compatible alias for v1 build_supervisor_system().
    Existing code that calls build_supervisor_system() will work unchanged.
    """
    return build_orchestrator(llm=llm, abort_event=abort_event)