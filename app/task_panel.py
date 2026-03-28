"""
task_panel.py — Live Task Execution Panel

Shows real-time task progress as the orchestrator runs:
  - DAG summary (which agents are running)
  - Per-task status (pending → running → done / failed)
  - Progress log stream
  - Expandable output per task
"""

from __future__ import annotations

import time
from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QPlainTextEdit,
    QSizePolicy,
)

_DARK    = "#080e18"
_CARD    = "#0d1825"
_BORDER  = "#1c2f47"
_ACCENT  = "#4f9de0"
_TEXT    = "#b8d0ee"
_SEC     = "#3a5a7a"
_SUCCESS = "#00d28c"
_WARN    = "#f0c040"
_ERR     = "#dc465a"
_RUN     = "#a070f0"

_AGENT_ICONS = {
    "research_agent": "🔍",
    "browser_agent":  "🌐",
    "system_agent":   "🖥",
    "file_agent":     "📁",
    "comms_agent":    "💬",
    "media_agent":    "🎵",
    "rag_agent":      "📚",
    "utility_agent":  "🔧",
}

_STATUS_COLOR = {
    "pending":        (_SEC,     "○ Pending"),
    "running":        (_RUN,     "● Running"),
    "done":           (_SUCCESS, "✓ Done"),
    "failed":         (_ERR,     "✗ Failed"),
    "waiting_human":  (_WARN,    "⏸ Waiting for you"),
    "skipped":        (_SEC,     "↷ Skipped"),
}


class TaskCard(QFrame):
    """Single task row showing agent, status, timing, and output snippet."""

    def __init__(self, task_id: str, agent: str, instruction: str, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self.agent = agent
        self._start_time = None
        self._done = False

        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(f"""
            TaskCard {{
                background: {_CARD}; border: 1px solid {_BORDER};
                border-radius: 8px; margin: 2px 0;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # Header row
        header = QHBoxLayout()
        icon = _AGENT_ICONS.get(agent, "⚙")
        self._icon_lbl = QLabel(f"{icon} {task_id}")
        self._icon_lbl.setStyleSheet(
            f"font-weight: 700; font-size: 9.5pt; color: {_ACCENT}; background: transparent;"
        )
        header.addWidget(self._icon_lbl)

        agent_lbl = QLabel(agent.replace("_", " ").title())
        agent_lbl.setStyleSheet(f"color: {_SEC}; font-size: 8.5pt; background: transparent;")
        header.addWidget(agent_lbl)
        header.addStretch()

        self._status_lbl = QLabel("○ Pending")
        self._status_lbl.setStyleSheet(f"color: {_SEC}; font-size: 8.5pt; font-weight: 600; background: transparent;")
        header.addWidget(self._status_lbl)

        self._time_lbl = QLabel("")
        self._time_lbl.setStyleSheet(f"color: {_SEC}; font-size: 8pt; background: transparent;")
        header.addWidget(self._time_lbl)
        layout.addLayout(header)

        # Instruction (truncated)
        instr = QLabel(instruction[:90] + ("…" if len(instruction) > 90 else ""))
        instr.setStyleSheet(f"color: {_TEXT}; font-size: 9pt; background: transparent;")
        instr.setWordWrap(True)
        layout.addWidget(instr)

        # Output snippet (hidden until done)
        self._output_lbl = QLabel("")
        self._output_lbl.setStyleSheet(
            f"color: {_SUCCESS}; font-size: 8.5pt; background: transparent; padding-top: 2px;"
        )
        self._output_lbl.setWordWrap(True)
        self._output_lbl.hide()
        layout.addWidget(self._output_lbl)

        # Timer for elapsed time display
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)

    def set_running(self):
        self._start_time = time.time()
        color, text = _STATUS_COLOR["running"]
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-size: 8.5pt; font-weight: 600; background: transparent;"
        )
        self._status_lbl.setText(text)
        self._timer.start()

    def set_done(self, output: str = "", duration: float = 0):
        self._done = True
        self._timer.stop()
        color, text = _STATUS_COLOR["done"]
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-size: 8.5pt; font-weight: 600; background: transparent;"
        )
        self._status_lbl.setText(text)
        self._time_lbl.setText(f"{duration:.1f}s")
        if output:
            snippet = output.strip()[:120].replace("\n", " ")
            self._output_lbl.setText(snippet + ("…" if len(output) > 120 else ""))
            self._output_lbl.show()

    def set_failed(self, error: str = "", duration: float = 0):
        self._done = True
        self._timer.stop()
        color, text = _STATUS_COLOR["failed"]
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-size: 8.5pt; font-weight: 600; background: transparent;"
        )
        self._status_lbl.setText(text)
        self._time_lbl.setText(f"{duration:.1f}s")
        if error:
            snippet = error[:80]
            self._output_lbl.setStyleSheet(
                f"color: {_ERR}; font-size: 8.5pt; background: transparent; padding-top: 2px;"
            )
            self._output_lbl.setText(snippet)
            self._output_lbl.show()

    def set_waiting_human(self):
        color, text = _STATUS_COLOR["waiting_human"]
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-size: 8.5pt; font-weight: 600; background: transparent;"
        )
        self._status_lbl.setText(text)

    def _tick(self):
        if self._start_time and not self._done:
            elapsed = time.time() - self._start_time
            self._time_lbl.setText(f"{elapsed:.0f}s…")


class TaskPanel(QWidget):
    """
    Live task execution panel — embedded in the chat UI.
    Shows real-time progress of all parallel tasks.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {_DARK};")
        self._cards: dict[str, TaskCard] = {}
        self._build()

    def _build(self):
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(4)

        # Header
        header = QHBoxLayout()
        title = QLabel("Task Execution")
        title.setStyleSheet(
            f"font-size: 10pt; font-weight: 700; color: {_ACCENT}; background: transparent;"
        )
        header.addWidget(title)
        header.addStretch()
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedHeight(24)
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid {_BORDER};
                border-radius: 4px; color: {_SEC}; font-size: 8pt; padding: 2px 8px;
            }}
            QPushButton:hover {{ border-color: {_ACCENT}; color: {_TEXT}; }}
        """)
        self._clear_btn.clicked.connect(self.clear)
        header.addWidget(self._clear_btn)
        self._main_layout.addLayout(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {_BORDER};")
        self._main_layout.addWidget(sep)

        # Scrollable cards area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_DARK}; border: none; }}"
            f"QScrollBar:vertical {{ background: {_DARK}; width: 5px; }}"
            f"QScrollBar::handle:vertical {{ background: {_BORDER}; border-radius: 2px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
        )
        self._cards_container = QWidget()
        self._cards_container.setStyleSheet(f"background: {_DARK};")
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(4)
        self._cards_layout.addStretch()
        scroll.setWidget(self._cards_container)
        self._main_layout.addWidget(scroll, stretch=1)

        # Log stream
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(100)
        self._log.setStyleSheet(f"""
            QPlainTextEdit {{
                background: #060c14; border: none;
                border-top: 1px solid {_BORDER};
                color: {_SEC}; font-family: 'Consolas', monospace;
                font-size: 8pt;
            }}
        """)
        self._main_layout.addWidget(self._log)

    # ── Public API ────────────────────────────────────────────────────────────

    def add_task(self, task_id: str, agent: str, instruction: str):
        """Add a task card when the DAG is built."""
        card = TaskCard(task_id, agent, instruction)
        self._cards[task_id] = card
        # Insert before the stretch
        count = self._cards_layout.count()
        self._cards_layout.insertWidget(count - 1, card)

    def set_task_running(self, task_id: str):
        if task_id in self._cards:
            self._cards[task_id].set_running()

    def set_task_done(self, task_id: str, output: str = "", duration: float = 0):
        if task_id in self._cards:
            self._cards[task_id].set_done(output, duration)

    def set_task_failed(self, task_id: str, error: str = "", duration: float = 0):
        if task_id in self._cards:
            self._cards[task_id].set_failed(error, duration)

    def set_task_waiting(self, task_id: str):
        if task_id in self._cards:
            self._cards[task_id].set_waiting_human()

    def log(self, msg: str):
        """Append a progress message to the log stream."""
        if msg.strip():
            self._log.appendPlainText(msg.rstrip())
            self._log.verticalScrollBar().setValue(
                self._log.verticalScrollBar().maximum()
            )

    def clear(self):
        """Remove all task cards and clear log."""
        for card in self._cards.values():
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._log.clear()

    def parse_progress_message(self, msg: str):
        """
        Parse scheduler progress messages and update cards automatically.
        Called by the progress_update signal from workers.
        """
        self.log(msg)

        # "→ Submitting T1 (research_agent): ..."
        import re
        m = re.search(r"Submitting (T\d+) \((\w+)\):", msg)
        if m:
            task_id, agent = m.group(1), m.group(2)
            if task_id not in self._cards:
                self.add_task(task_id, agent, msg.split(":")[-1].strip()[:80])
            self.set_task_running(task_id)
            return

        # "✓ T1 (research_agent) done in 3.2s"
        m = re.search(r"✓ (T\d+) \((\w+)\) done in ([\d.]+)s", msg)
        if m:
            self.set_task_done(m.group(1), "", float(m.group(3)))
            return

        # "✗ T1 (research_agent) failed: ..."
        m = re.search(r"✗ (T\d+) \((\w+)\) failed: (.+)", msg)
        if m:
            self.set_task_failed(m.group(1), m.group(3), 0)
            return
