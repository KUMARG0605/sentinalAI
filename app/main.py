"""
main.py — SentinelAI v2 Main Window

Upgrades from v1:
  - Live task execution panel (shows parallel agents running)
  - HITL dialog integration (product list, seat map, variants, payment confirm)
  - Progress streaming in real-time
  - Task DAG summary in UI
  - All existing features preserved
"""

import os
import subprocess
import sys
import html
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, Qt, QSize
from PyQt5.QtGui import QColor, QFont, QIcon, QPalette, QTextCursor
from PyQt5.QtWidgets import (
    QAction, QApplication, QCheckBox, QDialog, QFileDialog, QFrame,
    QGroupBox, QHBoxLayout, QFormLayout, QLabel, QListWidget, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QProgressBar,
    QSizePolicy, QSpacerItem, QSplitter, QStyle, QSystemTrayIcon,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget, QMenu, QScrollArea,
    QStackedWidget,
)

from app.src.path_utils import data_dir
from app.src.index_runtime import (
    clear_index_stop, read_index_status, request_index_stop, write_index_status
)
from app.ui.autostart import (
    enable_background_autostart, is_background_agent_running,
    launch_background_agent_if_not_running, stop_background_agents,
)
from app.ui.state import (
    get_index_lock_pid, load_app_state, release_index_lock, save_app_state
)
from app.ui.workers import (
    AssistantWorker, IndexStatusWorker, IndexerWorker, TextPromptWorker
)
from app.ui.hitl_dialog import make_hitl_dialog
from app.ui.task_panel import TaskPanel


# ─────────────────────────────────────────────────────────────────────────────
#  STYLESHEET  (inherited from v1, extended for v2 panels)
# ─────────────────────────────────────────────────────────────────────────────

APP_STYLE = """
QMainWindow, QDialog, QWidget {
    background-color: #080e18;
    color: #c8ddf5;
    font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Helvetica Neue', sans-serif;
    font-size: 10pt;
}
QGroupBox {
    border: 1px solid #1c2f47; border-radius: 10px;
    margin-top: 14px; padding-top: 8px;
    font-weight: 600; font-size: 9.5pt; color: #6a9fcb;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 14px; padding: 0 6px;
    color: #4f9de0; font-size: 9pt; letter-spacing: 0.06em; text-transform: uppercase;
}
QLabel { color: #b8d0ee; }
QLabel#heading { font-size: 22pt; font-weight: 700; color: #e8f4ff; letter-spacing: -0.02em; }
QLabel#subheading { font-size: 10pt; color: #4a7aaa; }
QLabel#statusBadge { font-size: 8.5pt; padding: 3px 10px; border-radius: 10px; font-weight: 600; }
QLabel#statusBadge[status="ready"]   { background: rgba(0,210,140,0.12); color: #00d28c; border: 1px solid rgba(0,210,140,0.3); }
QLabel#statusBadge[status="running"] { background: rgba(79,157,224,0.12); color: #4f9de0; border: 1px solid rgba(79,157,224,0.3); }
QLabel#statusBadge[status="error"]   { background: rgba(220,70,90,0.12);  color: #dc465a; border: 1px solid rgba(220,70,90,0.3); }
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser {
    background-color: #0d1825; border: 1px solid #1c2f47;
    border-radius: 8px; padding: 8px 12px; color: #d4e8ff;
    selection-background-color: #1e4a7a;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border: 1px solid #2e6aac; background-color: #0f1e2e;
}
QLineEdit#promptInput {
    font-size: 11pt; padding: 11px 16px; border-radius: 10px;
    border: 1.5px solid #1e3a5c; background-color: #0c1720; color: #e0efff;
}
QLineEdit#promptInput:focus { border: 1.5px solid #3d80c4; background-color: #0e1e30; }
QPushButton {
    background-color: #111e2e; border: 1px solid #1c3050;
    border-radius: 8px; padding: 8px 16px; color: #9fc5e8;
    font-weight: 500; font-size: 9.5pt;
}
QPushButton:hover { background-color: #172843; border-color: #2a5080; color: #c8e0ff; }
QPushButton:pressed { background-color: #0e1e30; }
QPushButton:disabled { color: #2a4060; border-color: #111e2e; }
QPushButton#primaryBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1b4e8a,stop:1 #1464b4);
    border: none; color: #e8f4ff; font-weight: 700; font-size: 10pt;
    padding: 10px 22px; border-radius: 10px;
}
QPushButton#primaryBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #225fa8,stop:1 #1a76d4);
}
QPushButton#dangerBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #6e1628,stop:1 #922040);
    border: none; color: #ffd8df; font-weight: 700; border-radius: 10px; padding: 10px 22px;
}
QPushButton#dangerBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8a1e34,stop:1 #b0284e);
}
QPushButton#successBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0a6646,stop:1 #0a8058);
    border: none; color: #c0fff0; font-weight: 700; border-radius: 10px; padding: 10px 22px;
}
QPushButton#sendBtn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1e5fa4,stop:1 #1470c8);
    border: none; border-radius: 10px; color: white; font-weight: 700;
    font-size: 10pt; min-width: 80px; padding: 11px 20px;
}
QPushButton#sendBtn:hover {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #2870c0,stop:1 #1a84e0);
}
QPushButton#sendBtn:disabled { background: #111e2e; color: #2a4060; }
QListWidget {
    background-color: #0d1825; border: 1px solid #1c2f47;
    border-radius: 8px; color: #a0c0e0; padding: 4px;
}
QListWidget::item { padding: 5px 8px; border-radius: 4px; }
QListWidget::item:selected { background-color: #1a3a60; color: #c8e4ff; }
QListWidget::item:hover { background-color: #122030; }
QProgressBar {
    border: 1px solid #1c2f47; border-radius: 6px; background-color: #0a1220;
    text-align: center; color: #6090c0; font-size: 8pt; height: 8px;
}
QProgressBar::chunk {
    border-radius: 5px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1464b4,stop:1 #00c896);
}
QScrollBar:vertical { background: #080e18; width: 6px; margin: 0; border-radius: 3px; }
QScrollBar::handle:vertical { background: #1c3050; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #2a4878; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QTextBrowser {
    background-color: #080e18; border: none;
    color: #b8d0ee; font-size: 10.5pt; line-height: 1.6;
}
QSplitter::handle { background: #1c2f47; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical   { height: 1px; }
QCheckBox { color: #8ab4d8; spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid #2a4060; background: #0d1825;
}
QCheckBox::indicator:checked { background: #1464b4; border-color: #1464b4; }
"""

_MODE_META = {
    "idle":    {"dot": "#2a4870", "text": "#2a4870",  "label": "Idle"},
    "wake":    {"dot": "#f0c040", "text": "#c8a030",  "label": "Listening for wake word…"},
    "listen":  {"dot": "#4fc8f0", "text": "#4fb8e0",  "label": "Listening…"},
    "process": {"dot": "#a070f0", "text": "#8860d8",  "label": "Processing…"},
    "speak":   {"dot": "#00d28c", "text": "#00b87a",  "label": "Speaking…"},
    "talk":    {"dot": "#00d28c", "text": "#00b87a",  "label": "Speaking…"},
}


class StatusIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "idle"
        self._pulse_on = True
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 4, 0, 4)
        layout.setSpacing(10)
        self._dot = QLabel("●")
        self._dot.setFixedWidth(18)
        self._dot.setAlignment(Qt.AlignCenter)
        self._dot.setStyleSheet("background: transparent;")
        self._lbl = QLabel("Idle")
        self._lbl.setStyleSheet("font-size: 10pt; font-weight: 600; background: transparent;")
        layout.addWidget(self._dot)
        layout.addWidget(self._lbl)
        layout.addStretch()
        self._timer = QTimer(self)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self._pulse)
        self._timer.start()
        self.set_mode("idle")

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        meta = _MODE_META.get(mode, _MODE_META["idle"])
        self._dot.setStyleSheet(f"font-size: 14pt; color: {meta['dot']}; background: transparent;")
        self._lbl.setStyleSheet(
            f"font-size: 10pt; font-weight: 600; color: {meta['text']}; background: transparent;"
        )
        self._lbl.setText(meta["label"])
        self._pulse_on = True

    def _pulse(self):
        if self._mode == "idle":
            return
        self._pulse_on = not self._pulse_on
        meta = _MODE_META.get(self._mode, _MODE_META["idle"])
        color = meta["dot"] if self._pulse_on else "#1a2e48"
        self._dot.setStyleSheet(f"font-size: 14pt; color: {color}; background: transparent;")


def _chat_html_user(text: str) -> str:
    return f"""
    <div style="margin:10px 0;display:flex;justify-content:flex-end;">
      <div style="background:#112240;border:1px solid #1e3e6a;border-radius:14px 14px 4px 14px;
        padding:10px 16px;max-width:82%;color:#d0e8ff;font-size:10.5pt;line-height:1.55;">
        {html.escape(text)}</div></div>"""

def _chat_html_assistant(text: str) -> str:
    return f"""
    <div style="margin:10px 0;">
      <div style="display:inline-block;background:#0e1e30;border:1px solid #1a3050;
        border-radius:14px 14px 14px 4px;padding:10px 16px;max-width:82%;
        color:#c0daf8;font-size:10.5pt;line-height:1.55;">
        <span style="color:#3a7abf;font-size:8.5pt;font-weight:600;display:block;margin-bottom:4px;">
          ⬡ SENTINEL</span>
        {html.escape(text)}</div></div>"""

def _chat_html_system(text: str) -> str:
    return f"""
    <div style="margin:6px 0;text-align:center;">
      <span style="font-size:8.5pt;color:#2a4870;font-style:italic;">{html.escape(text)}</span>
    </div>"""

def _chat_html_progress(text: str) -> str:
    return f"""
    <div style="margin:3px 0 3px 12px;">
      <span style="font-size:8pt;color:#2a4870;font-family:monospace;">{html.escape(text)}</span>
    </div>"""


# ─────────────────────────────────────────────────────────────────────────────
#  CONVERSATION WINDOW  (voice sessions)
# ─────────────────────────────────────────────────────────────────────────────

class ConversationWindow(QDialog):
    def __init__(self, on_end_requested, parent=None):
        super().__init__(parent)
        self._on_end_requested = on_end_requested
        self.setWindowTitle("Sentinel — Voice Session")
        self.resize(440, 240)
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._build_ui()

    def _build_ui(self):
        card = QWidget(self)
        card.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(card)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(24, 20, 24, 20)
        inner.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("Voice Session")
        title.setStyleSheet("font-weight:700;font-size:12pt;color:#e0f0ff;")
        top.addWidget(title)
        top.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self._on_end_clicked)
        top.addWidget(close_btn)
        inner.addLayout(top)

        self.status_indicator = StatusIndicator()
        inner.addWidget(self.status_indicator)

        self.transcript_label = QLabel("")
        self.transcript_label.setStyleSheet("color:#4a7aaa;font-size:9pt;")
        self.transcript_label.setWordWrap(True)
        inner.addWidget(self.transcript_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#0f1e2e;")
        inner.addWidget(sep)

        end_btn = QPushButton("End Session")
        end_btn.setObjectName("dangerBtn")
        end_btn.setFixedHeight(36)
        end_btn.clicked.connect(self._on_end_clicked)
        inner.addWidget(end_btn, alignment=Qt.AlignRight)

        self.last_text = self.transcript_label
        self.setStyleSheet(APP_STYLE + """
            QWidget#card {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #0d1e30,stop:1 #080e18);
                border: 1px solid #1e3a5c; border-radius: 16px;
            }
            QPushButton#closeBtn {
                background:transparent;border:none;color:#2a4870;
                font-size:12pt;border-radius:14px;
            }
            QPushButton#closeBtn:hover { background:#1a2a40;color:#c0d8f0; }
        """)

    def _on_end_clicked(self):
        if callable(self._on_end_requested):
            self._on_end_requested()
        self.hide()

    def _on_user_spoke(self, text: str):
        if text:
            self.transcript_label.setText(f'You: "{text}"')
        else:
            self.transcript_label.setText("")

    def closeEvent(self, event):
        self._on_end_clicked()
        event.ignore()


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN WINDOW
# ─────────────────────────────────────────────────────────────────────────────

class SentinelMainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SentinelAI v2")
        self.resize(1280, 820)
        self.state = load_app_state()
        self.indexer_worker = None
        self.status_worker = None
        self.settings_dialog = None
        self._active_prompt_workers: list[TextPromptWorker] = []
        self.root_folder = self.state.get("root_folder", "C:/")
        self._thinking_inserted = False
        self._wake_log_offset = 0          # byte offset for incremental tail
        self._wake_log_timer  = None       # QTimer for live polling

        self._build_ui()
        self.setStyleSheet(APP_STYLE)
        self._load_state_into_ui()
        self._update_wake_word_btn_state()
        self._start_wake_log_timer()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_sidebar())
        root_layout.addWidget(self._build_content(), stretch=1)

    def _build_sidebar(self):
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("QWidget#sidebar{background:#060c14;border-right:1px solid #0f1e2e;}")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 22, 14, 18)
        layout.setSpacing(3)

        logo = QLabel("SENTINEL")
        logo.setStyleSheet(
            "font-size:15pt;font-weight:800;color:#4f9de0;"
            "letter-spacing:0.14em;background:transparent;border:none;"
        )
        layout.addWidget(logo)
        sub = QLabel("AI Desktop Assistant v2")
        sub.setStyleSheet("font-size:8pt;color:#2a4870;padding-bottom:14px;background:transparent;border:none;")
        layout.addWidget(sub)

        NAV_STYLE = """
            QPushButton { background:transparent;border:none;border-radius:7px;
                text-align:left;padding:0 0 0 14px;color:#3a5a7a;
                font-size:9.5pt;font-weight:500; }
            QPushButton:hover { background:#0d1a28;color:#7aabda; }
            QPushButton:checked { background:#0f2035;color:#4f9de0;font-weight:700; }
        """
        def nav_btn(label):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFixedHeight(34)
            b.setStyleSheet(NAV_STYLE)
            return b

        self._nav_chat = nav_btn("  Assistant")
        self._nav_tasks = nav_btn("  Live Tasks")
        self._nav_index = nav_btn("  Index & Scan")
        self._nav_wake_log = nav_btn("  Wake Logs")
        self._nav_settings_btn = nav_btn("  Settings")
        self._nav_chat.setChecked(True)

        self._nav_chat.clicked.connect(lambda: self._switch_panel(0))
        self._nav_tasks.clicked.connect(lambda: self._switch_panel(1))
        self._nav_index.clicked.connect(lambda: self._switch_panel(2))
        self._nav_wake_log.clicked.connect(lambda: self._switch_panel(3))
        self._nav_settings_btn.clicked.connect(self._open_settings_dialog)

        for b in [self._nav_chat, self._nav_tasks, self._nav_index, self._nav_wake_log, self._nav_settings_btn]:
            layout.addWidget(b)

        layout.addSpacing(18)

        # Wake word card
        ww_card = QFrame()
        ww_card.setObjectName("wwCard")
        ww_card.setFrameShape(QFrame.NoFrame)
        ww_card.setStyleSheet("QFrame#wwCard{background:#0a1620;border:1px solid #162840;border-radius:10px;}")
        ww_layout = QVBoxLayout(ww_card)
        ww_layout.setContentsMargins(12, 10, 12, 12)
        ww_layout.setSpacing(5)
        ww_title = QLabel("WAKE WORD")
        ww_title.setStyleSheet("font-size:7.5pt;color:#2a4870;font-weight:700;letter-spacing:0.10em;background:transparent;border:none;")
        self.wake_word_status_label = QLabel("  Stopped")
        self.wake_word_status_label.setStyleSheet("color:#dc465a;font-size:9.5pt;font-weight:600;background:transparent;border:none;")
        self.toggle_wake_word_btn = QPushButton("Start Detection")
        self.toggle_wake_word_btn.setObjectName("wwBtn")
        self.toggle_wake_word_btn.setMinimumHeight(32)
        self._style_ww_btn(False)
        self.toggle_wake_word_btn.clicked.connect(self._toggle_wake_word_detection)
        ww_layout.addWidget(ww_title)
        ww_layout.addWidget(self.wake_word_status_label)
        ww_layout.addSpacing(3)
        ww_layout.addWidget(self.toggle_wake_word_btn)
        layout.addWidget(ww_card)
        layout.addStretch()
        ver = QLabel("v2.0")
        ver.setAlignment(Qt.AlignCenter)
        ver.setStyleSheet("font-size:8pt;color:#142030;background:transparent;border:none;")
        layout.addWidget(ver)
        return sidebar

    def _build_content(self):
        content = QWidget()
        content.setStyleSheet("QWidget{background:#080e18;}")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_topbar())
        self._panels = QStackedWidget()
        self._panels.addWidget(self._build_chat_panel())     # 0 — assistant
        self._panels.addWidget(self._build_task_panel())     # 1 — live tasks
        self._panels.addWidget(self._build_index_panel())    # 2 — index
        self._panels.addWidget(self._build_wake_log_panel()) # 3 — wake logs
        layout.addWidget(self._panels, stretch=1)
        return content

    def _build_topbar(self):
        bar = QWidget()
        bar.setFixedHeight(52)
        bar.setStyleSheet("QWidget{background:#060c14;border-bottom:1px solid #0f1e2e;}")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 20, 0)
        self._panel_title = QLabel("Assistant")
        self._panel_title.setStyleSheet("font-size:12pt;font-weight:700;color:#7aabda;")
        layout.addWidget(self._panel_title)
        layout.addStretch()
        self._status_badge = QLabel("Ready")
        self._status_badge.setObjectName("statusBadge")
        self._status_badge.setProperty("status", "ready")
        self._status_badge.style().unpolish(self._status_badge)
        self._status_badge.style().polish(self._status_badge)
        layout.addWidget(self._status_badge)
        settings_btn = QPushButton("⚙  Settings")
        settings_btn.setFixedHeight(30)
        settings_btn.clicked.connect(self._open_settings_dialog)
        layout.addWidget(settings_btn)
        return bar

    def _build_chat_panel(self):
        """Chat + right-side task panel, split view."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Splitter: chat on left, mini task panel on right
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Chat area
        chat_widget = QWidget()
        chat_layout = QVBoxLayout(chat_widget)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)

        self.chat_display = QTextBrowser()
        self.chat_display.setOpenLinks(False)
        self.chat_display.setStyleSheet(
            "QTextBrowser{background:#080e18;border:none;padding:20px 28px;font-size:10.5pt;}"
        )
        self.chat_display.setHtml("""
            <div style='text-align:center;margin-top:60px;'>
              <div style='font-size:36pt;margin-bottom:12px;'>⬡</div>
              <div style='font-size:16pt;font-weight:700;color:#1e3a5c;'>SentinelAI v2</div>
              <div style='font-size:10pt;color:#142030;margin-top:8px;'>
                Multi-agent AI • Parallel execution • Type or speak your command
              </div>
            </div>
        """)
        chat_layout.addWidget(self.chat_display, stretch=1)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color:#0f1e2e;margin:0;")
        chat_layout.addWidget(divider)

        input_area = QWidget()
        input_area.setStyleSheet("QWidget{background:#060c14;}")
        input_layout = QVBoxLayout(input_area)
        input_layout.setContentsMargins(20, 10, 20, 16)
        input_layout.setSpacing(8)

        self.chat_status = QLabel("Ready  ·  Type a prompt or say the wake word")
        self.chat_status.setStyleSheet("font-size:8.5pt;color:#1e3a5c;")
        input_layout.addWidget(self.chat_status)

        prompt_row = QHBoxLayout()
        prompt_row.setSpacing(10)
        self.prompt_input = QLineEdit()
        self.prompt_input.setObjectName("promptInput")
        self.prompt_input.setPlaceholderText("Type any command… order food, book tickets, search YouTube, download files…")
        self.prompt_input.setFixedHeight(44)
        self.prompt_input.returnPressed.connect(self._submit_prompt)
        prompt_row.addWidget(self.prompt_input, stretch=1)

        self.send_btn = QPushButton("Send  ›")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(90, 44)
        self.send_btn.clicked.connect(self._submit_prompt)
        prompt_row.addWidget(self.send_btn)

        self.clear_chat_btn = QPushButton("✕")
        self.clear_chat_btn.setFixedSize(36, 44)
        self.clear_chat_btn.setToolTip("Clear chat")
        self.clear_chat_btn.setStyleSheet("""
            QPushButton{background:transparent;border:1px solid #0f1e2e;
                border-radius:8px;color:#1e3a5c;font-size:11pt;}
            QPushButton:hover{border-color:#2a4870;color:#4a6a8a;background:#0a1420;}
        """)
        self.clear_chat_btn.clicked.connect(self._clear_chat)
        prompt_row.addWidget(self.clear_chat_btn)
        input_layout.addLayout(prompt_row)
        chat_layout.addWidget(input_area)

        splitter.addWidget(chat_widget)

        # Right: mini task panel in chat view
        self._mini_task_panel = TaskPanel()
        self._mini_task_panel.setMinimumWidth(260)
        self._mini_task_panel.setMaximumWidth(340)
        splitter.addWidget(self._mini_task_panel)
        splitter.setSizes([860, 280])

        layout.addWidget(splitter, stretch=1)
        return panel

    def _build_task_panel(self):
        """Full-page live task execution panel."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        heading = QLabel("Live Task Execution")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        sub = QLabel("Real-time view of all parallel agent tasks for the current prompt.")
        sub.setObjectName("subheading")
        layout.addWidget(sub)
        self._full_task_panel = TaskPanel()
        layout.addWidget(self._full_task_panel, stretch=1)
        return panel

    def _build_index_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        heading = QLabel("Index & Scan")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        sub = QLabel("Build and maintain the vector database from your local files.")
        sub.setObjectName("subheading")
        layout.addWidget(sub)

        scan_group = QGroupBox("Scan Source")
        scan_layout = QVBoxLayout(scan_group)
        scan_layout.setSpacing(10)
        root_row = QHBoxLayout()
        self.root_folder_label = QLabel("Root folder: C:/")
        self.root_folder_label.setStyleSheet("color:#4f9de0;font-weight:600;")
        choose_root = QPushButton("Change Folder")
        choose_root.clicked.connect(self._choose_root_folder)
        root_row.addWidget(self.root_folder_label)
        root_row.addStretch()
        root_row.addWidget(choose_root)
        scan_layout.addLayout(root_row)
        scan_layout.addWidget(QLabel("Excluded folders"))
        self.exclude_list = QListWidget()
        self.exclude_list.setMaximumHeight(80)
        scan_layout.addWidget(self.exclude_list)
        excl_row = QHBoxLayout()
        add_e = QPushButton("Add Exclusion")
        add_e.clicked.connect(self._add_exclusion)
        rem_e = QPushButton("Remove Selected")
        rem_e.clicked.connect(self._remove_exclusion)
        excl_row.addWidget(add_e)
        excl_row.addWidget(rem_e)
        excl_row.addStretch()
        scan_layout.addLayout(excl_row)
        layout.addWidget(scan_group)

        ctrl_group = QGroupBox("Controls")
        ctrl_layout = QVBoxLayout(ctrl_group)
        ctrl_layout.setSpacing(10)
        btn_row = QHBoxLayout()
        self.start_index_btn = QPushButton("▶  Build Vector DB")
        self.start_index_btn.setObjectName("successBtn")
        self.start_index_btn.clicked.connect(self._start_indexing)
        self.stop_index_btn = QPushButton("■  Stop Indexing")
        self.stop_index_btn.setObjectName("dangerBtn")
        self.stop_index_btn.setEnabled(False)
        self.stop_index_btn.clicked.connect(self._stop_indexing)
        self.rescan_btn = QPushButton("↻  Rescan")
        self.rescan_btn.clicked.connect(self._rescan_indexing)
        self.check_status_btn = QPushButton("ℹ  DB Status")
        self.check_status_btn.clicked.connect(self._check_status)
        for b in [self.start_index_btn, self.stop_index_btn, self.rescan_btn, self.check_status_btn]:
            btn_row.addWidget(b)
        ctrl_layout.addLayout(btn_row)
        self.progress = QProgressBar()
        ctrl_layout.addWidget(self.progress)
        self.progress_label = QLabel("Completed: 0  ·  Pending: 0")
        self.progress_label.setStyleSheet("font-size:8.5pt;color:#2a4870;")
        ctrl_layout.addWidget(self.progress_label)
        layout.addWidget(ctrl_group)

        log_group = QGroupBox("Live Log")
        log_layout = QVBoxLayout(log_group)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("font-family:Consolas,'Courier New',monospace;font-size:9pt;")
        log_layout.addWidget(self.log_box)
        layout.addWidget(log_group, stretch=1)
        return panel

    def _build_wake_log_panel(self):
        """Live wake-word diagnostic panel."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        heading = QLabel("Wake Word Diagnostics")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        sub = QLabel(
            "Live log from the background wake word detector. Lines stream automatically every second."
        )
        sub.setObjectName("subheading")
        layout.addWidget(sub)

        # ── Status row ────────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        self._wl_status_badge = QLabel("● UNKNOWN")
        self._wl_status_badge.setStyleSheet(
            "font-size:9pt;font-weight:700;color:#2a4870;"
            "background:#0a1620;border:1px solid #162840;"
            "border-radius:8px;padding:3px 10px;"
        )
        self._wl_log_path_label = QLabel("")
        self._wl_log_path_label.setStyleSheet("font-size:8pt;color:#1e3a5c;")
        status_row.addWidget(self._wl_status_badge)
        status_row.addSpacing(12)
        status_row.addWidget(self._wl_log_path_label)
        status_row.addStretch()

        refresh_btn = QPushButton("↻  Refresh Now")
        refresh_btn.setFixedHeight(28)
        refresh_btn.clicked.connect(self._poll_wake_log)
        status_row.addWidget(refresh_btn)

        clear_btn = QPushButton("✕  Clear")
        clear_btn.setFixedHeight(28)
        clear_btn.clicked.connect(self._clear_wake_log)
        status_row.addWidget(clear_btn)
        layout.addLayout(status_row)

        # ── Log viewer ────────────────────────────────────────────────────────
        log_group = QGroupBox("Background Process Log  (wake.log)")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(8, 8, 8, 8)

        self._wake_log_box = QPlainTextEdit()
        self._wake_log_box.setReadOnly(True)
        self._wake_log_box.setStyleSheet(
            "QPlainTextEdit{"
            "  font-family:Consolas,'Courier New',monospace;"
            "  font-size:9pt;"
            "  background:#050b12;"
            "  border:none;"
            "  color:#7a9cbe;"
            "}"
        )
        log_layout.addWidget(self._wake_log_box)
        layout.addWidget(log_group, stretch=1)
        return panel

    # ── Panel switching ───────────────────────────────────────────────────────

    def _switch_panel(self, index: int):
        self._panels.setCurrentIndex(index)
        titles = {0: "Assistant", 1: "Live Tasks", 2: "Index & Scan", 3: "Wake Logs"}
        self._panel_title.setText(titles.get(index, ""))
        nav_btns = [self._nav_chat, self._nav_tasks, self._nav_index, self._nav_wake_log]
        for i, b in enumerate(nav_btns):
            b.setChecked(i == index)

    # ── Prompt submission ─────────────────────────────────────────────────────

    def _submit_prompt(self):
        query = self.prompt_input.text().strip()
        if not query:
            return

        self.prompt_input.clear()
        self.send_btn.setEnabled(False)
        self.prompt_input.setEnabled(False)
        self._set_status("Planning…", "running")
        self._thinking_inserted = False

        # Clear task panels for new prompt
        self._mini_task_panel.clear()
        self._full_task_panel.clear()

        self.chat_display.append(_chat_html_user(query))
        self._scroll_chat()

        worker = TextPromptWorker(query)
        worker.started_processing.connect(self._on_started)
        worker.response_ready.connect(self._on_response)
        worker.progress_update.connect(self._on_progress)
        worker.hitl_question.connect(self._on_hitl_question)
        worker.status_changed.connect(lambda s: self._set_status(s, "running"))
        worker.failed.connect(self._on_error)
        worker.finished.connect(lambda: self._on_worker_done(worker))
        self._active_prompt_workers.append(worker)
        worker.start()

    def _on_started(self, query: str):
        self.chat_display.append(_chat_html_system("⬡ Planning tasks…"))
        self._scroll_chat()

    def _on_progress(self, msg: str):
        # Forward to both task panels
        self._mini_task_panel.parse_progress_message(msg)
        self._full_task_panel.parse_progress_message(msg)
        # Show brief progress in chat
        if any(k in msg for k in ["Submitting", "✓", "✗", "Planning"]):
            self.chat_display.append(_chat_html_progress(msg.replace("[Orchestrator] ", "").replace("[Scheduler] ", "")))
            self._scroll_chat()

    def _on_response(self, answer: str, sources: list):
        self.chat_display.append(_chat_html_assistant(answer))
        if sources:
            agents = " · ".join(
                s.get("source", "") for s in sources[:4] if s.get("source")
            )
            if agents:
                self.chat_display.append(_chat_html_system(f"Agents: {agents}"))
        self._scroll_chat()
        self._set_status("Ready", "ready")

    def _on_error(self, err: str):
        self.chat_display.append(_chat_html_system(f"Error: {err[:200]}"))
        self._scroll_chat()
        self._set_status("Error", "error")

    def _on_worker_done(self, worker: TextPromptWorker):
        if worker in self._active_prompt_workers:
            self._active_prompt_workers.remove(worker)
        self.send_btn.setEnabled(True)
        self.prompt_input.setEnabled(True)
        self.prompt_input.setFocus()

    # ── HITL dialog ───────────────────────────────────────────────────────────

    def _on_hitl_question(self, question: dict):
        """Show the appropriate dialog and return user's answer to the worker."""
        stype = question.get("selection_type", "text")
        qtext = question.get("question", "")[:60]
        self.chat_display.append(
            _chat_html_system(f"⏸ Waiting for your input: {qtext}…")
        )
        self._scroll_chat()

        # Mark task as waiting
        task_card_id = None  # could extract from question metadata if needed
        self._mini_task_panel.log(f"⏸ Agent waiting: {qtext[:50]}")

        dialog = make_hitl_dialog(question, self)
        dialog.answered.connect(
            lambda ans: TextPromptWorker.answer_hitl(question["id"], ans)
        )
        dialog.answered.connect(
            lambda ans: self.chat_display.append(
                _chat_html_system(f"✓ You selected: {ans[:60]}")
            )
        )
        dialog.exec_()

    # ── Chat helpers ──────────────────────────────────────────────────────────

    def _clear_chat(self):
        self.chat_display.setHtml("""
            <div style='text-align:center;margin-top:60px;'>
              <div style='font-size:28pt;margin-bottom:10px;'>⬡</div>
              <div style='font-size:13pt;color:#1e3a5c;'>Chat cleared</div>
            </div>
        """)
        self._mini_task_panel.clear()

    def _scroll_chat(self):
        sb = self.chat_display.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Status helpers ────────────────────────────────────────────────────────

    def _set_status(self, text: str, tone: str = "ready"):
        self._status_badge.setText(text)
        self._status_badge.setProperty("status", tone)
        self._status_badge.style().unpolish(self._status_badge)
        self._status_badge.style().polish(self._status_badge)
        self.chat_status.setText(text)

    def _append_log(self, message: str):
        self.log_box.appendPlainText(message)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    # ── Wake word ─────────────────────────────────────────────────────────────

    def _style_ww_btn(self, running: bool):
        if running:
            self.toggle_wake_word_btn.setStyleSheet("""
                QPushButton#wwBtn {
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #6e1628,stop:1 #922040);
                    border:none;border-radius:7px;color:#ffd8df;font-weight:700;font-size:9pt;padding:5px 10px;
                }
                QPushButton#wwBtn:hover {
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8a1e34,stop:1 #b0284e);
                }
            """)
        else:
            self.toggle_wake_word_btn.setStyleSheet("""
                QPushButton#wwBtn {
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0a6646,stop:1 #0a8058);
                    border:none;border-radius:7px;color:#c0fff0;font-weight:700;font-size:9pt;padding:5px 10px;
                }
                QPushButton#wwBtn:hover {
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0d7d56,stop:1 #0d9c6c);
                }
            """)

    def _update_wake_word_btn_state(self):
        running = self._is_wake_word_running()
        if running:
            self.wake_word_status_label.setText("  Running")
            self.wake_word_status_label.setStyleSheet(
                "color:#00d28c;font-size:9.5pt;font-weight:600;background:transparent;border:none;"
            )
            self.toggle_wake_word_btn.setText("Stop Detection")
            self._style_ww_btn(True)
        else:
            self.wake_word_status_label.setText("  Stopped")
            self.wake_word_status_label.setStyleSheet(
                "color:#dc465a;font-size:9.5pt;font-weight:600;background:transparent;border:none;"
            )
            self.toggle_wake_word_btn.setText("Start Detection")
            self._style_ww_btn(False)

    def _toggle_wake_word_detection(self):
        if self._is_wake_word_running():
            try:
                self._stop_independent_wake_word_process()
                self._append_log("Wake word detection stopped.")
            except Exception as e:
                self._append_log(f"Failed to stop: {e}")
            self._update_wake_word_btn_state()
        else:
            try:
                self._start_independent_wake_word_process()
                self._append_log("Wake word detection started.")
                
                # Optimistic update: Show "Starting..." because PowerShell launches the daemon asynchronously
                self.wake_word_status_label.setText("  Starting...")
                self.wake_word_status_label.setStyleSheet(
                    "color:#ffbe0a;font-size:9.5pt;font-weight:600;background:transparent;border:none;"
                )
                self.toggle_wake_word_btn.setText("Stop Detection")
                self._style_ww_btn(True)
                
                # Check status gracefully after 1.5 seconds when the .exe has fully hydrated into tasklist
                QTimer.singleShot(1500, self._update_wake_word_btn_state)
            except Exception as e:
                self._append_log(f"Failed to start: {e}")
                self._update_wake_word_btn_state()

    def _is_wake_word_running(self) -> bool:
        try:
            self.state = load_app_state()
            pid = self.state.get("wake_word_pid")
            if pid:
                result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
                if str(pid) in result.stdout:
                    return True
                self.state["wake_word_pid"] = None
                save_app_state(self.state)
            return is_background_agent_running()
        except Exception:
            return False

    def _start_independent_wake_word_process(self):
        from app.ui.autostart import launch_background_agent_if_not_running
        launch_background_agent_if_not_running()
        self.state["background_enabled"] = True
        save_app_state(self.state)

    def _stop_independent_wake_word_process(self):
        from app.ui.autostart import stop_background_agents
        stop_background_agents()
        self.state["background_enabled"] = False
        self.state["wake_word_pid"] = None
        save_app_state(self.state)

    # ── Wake Log helpers ──────────────────────────────────────────────────────

    def _wake_log_path_resolve(self):
        """Return Path to wake.log regardless of frozen/dev mode."""
        try:
            from app.src.path_utils import data_dir
            return data_dir().parent / "logs" / "wake.log"
        except Exception:
            import os
            appdata = os.getenv("APPDATA", str(Path.home()))
            return Path(appdata) / "SentinelAI" / "logs" / "wake.log"

    def _start_wake_log_timer(self):
        self._wake_log_timer = QTimer(self)
        self._wake_log_timer.setInterval(1000)
        self._wake_log_timer.timeout.connect(self._poll_wake_log)
        self._wake_log_timer.start()

    def _poll_wake_log(self):
        """Append new lines from wake.log to the log viewer and update status badge."""
        # ── update status badge ───────────────────────────────────────────────
        try:
            from app.src.index_runtime import read_wake_status
            ws = read_wake_status() or {}
            ws_state = ws.get("state", "unknown")
            ws_pid   = ws.get("pid", "")
            badge_text  = f"● {ws_state.upper()}"
            badge_extra = f"  (pid {ws_pid})" if ws_pid else ""
            badge_color = {
                "listening":     "#00d28c",
                "starting":      "#f0c040",
                "wake_detected": "#4fc8f0",
                "transcribing":  "#4fc8f0",
                "thinking":      "#a070f0",
                "speaking":      "#00d28c",
                "stopped":       "#dc465a",
                "error":         "#dc465a",
            }.get(ws_state, "#2a4870")
            self._wl_status_badge.setText(badge_text + badge_extra)
            self._wl_status_badge.setStyleSheet(
                f"font-size:9pt;font-weight:700;color:{badge_color};"
                "background:#0a1620;border:1px solid #162840;"
                "border-radius:8px;padding:3px 10px;"
            )
        except Exception:
            pass

        # ── tail wake.log for new lines ───────────────────────────────────────
        try:
            log_path = self._wake_log_path_resolve()
            if not hasattr(self, "_wake_log_path_cache"):
                self._wake_log_path_cache = log_path
                self._wl_log_path_label.setText(str(log_path))

            if not log_path.exists():
                if self._wake_log_offset == 0:
                    self._append_wake_line(
                        "[INFO] wake.log not found yet — start wake word detection to begin.",
                        "#2a4870"
                    )
                    self._wake_log_offset = -1  # sentinel: shown hint
                return

            file_size = log_path.stat().st_size
            if self._wake_log_offset < 0:
                # File appeared after hint — reset
                self._wake_log_offset = 0

            if file_size < self._wake_log_offset:
                # File was rotated/truncated
                self._wake_log_offset = 0

            if file_size == self._wake_log_offset:
                return  # nothing new

            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._wake_log_offset)
                new_text = fh.read()
                self._wake_log_offset = fh.tell()

            for raw_line in new_text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                ll = line.lower()
                if any(k in ll for k in ["error", "fatal", "exception", "traceback"]):
                    color = "#dc465a"
                elif any(k in ll for k in ["warn", "stop", "stopping", "stopped"]):
                    color = "#f0c040"
                elif any(k in ll for k in ["wake word detected", "ready", "done"]):
                    color = "#00d28c"
                elif any(k in ll for k in ["listening", "starting", "loading", "wake-py"]):
                    color = "#4fc8f0"
                else:
                    color = "#7a9cbe"
                self._append_wake_line(line, color)

        except Exception as e:
            self._append_wake_line(f"[UI] Error reading log: {e}", "#dc465a")

    def _append_wake_line(self, text: str, color: str = "#7a9cbe"):
        from PyQt5.QtGui import QTextCharFormat, QColor
        cursor = self._wake_log_box.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(text + "\n", fmt)
        self._wake_log_box.setTextCursor(cursor)
        self._wake_log_box.verticalScrollBar().setValue(
            self._wake_log_box.verticalScrollBar().maximum()
        )

    def _clear_wake_log(self):
        self._wake_log_box.clear()
        self._wake_log_offset = 0  # re-read from start on next poll

    # ── Indexing ──────────────────────────────────────────────────────────────

    def _choose_root_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Root Folder")
        if folder:
            self.root_folder = folder
            self.root_folder_label.setText(f"Root folder: {folder}")
            self._persist_ui_config()

    def _add_exclusion(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Exclude")
        if folder:
            self.exclude_list.addItem(folder)
            self._persist_ui_config()

    def _remove_exclusion(self):
        for item in self.exclude_list.selectedItems():
            self.exclude_list.takeItem(self.exclude_list.row(item))
        self._persist_ui_config()

    def _start_indexing(self):
        if self.indexer_worker and self.indexer_worker.isRunning():
            return
        self.start_index_btn.setEnabled(False)
        self.stop_index_btn.setEnabled(True)
        self.progress.setValue(0)
        self._append_log("Starting indexer…")
        index_path = str(data_dir() / "faiss_index")
        self.indexer_worker = IndexerWorker(
            self.root_folder, index_path,
            [self.exclude_list.item(i).text() for i in range(self.exclude_list.count())]
        )
        self.indexer_worker.log.connect(self._append_log)
        self.indexer_worker.progress.connect(self._on_index_progress)
        self.indexer_worker.completed.connect(self._on_index_completed)
        self.indexer_worker.failed.connect(self._on_index_failed)
        self.indexer_worker.start()
        self._set_indexing_state(True)

    def _stop_indexing(self):
        if self.indexer_worker and self.indexer_worker.isRunning():
            self.indexer_worker.request_stop()
            self._append_log("Stopping indexer…")
            self.start_index_btn.setEnabled(True)
            self.stop_index_btn.setEnabled(False)
            self._set_indexing_state(False)

    def _rescan_indexing(self):
        if self.indexer_worker and self.indexer_worker.isRunning():
            QMessageBox.warning(self, "Indexing", "Indexing is already running.")
            return
        self._start_indexing()

    def _check_status(self):
        try:
            status = read_index_status()
            self._append_log(f"DB Status: {status}")
        except Exception as e:
            self._append_log(f"Error: {e}")

    def _set_indexing_state(self, in_progress: bool):
        self._persist_ui_config()
        self.state["consent_given"] = True
        self.state["indexing_in_progress"] = bool(in_progress)
        save_app_state(self.state)

    def _on_index_progress(self, info: dict):
        total = info.get("total_new", 0)
        completed = info.get("completed_new", 0)
        pending = info.get("pending_new", 0)
        if total > 0:
            pct = int((completed / total) * 100)
            self.progress.setValue(pct)
        else:
            self.progress.setValue(0)
        self.progress_label.setText(f"Completed: {completed}  ·  Pending: {pending}")

    def _on_index_completed(self, result: dict):
        self.start_index_btn.setEnabled(True)
        self.stop_index_btn.setEnabled(False)
        self.progress.setValue(100)
        self._append_log("Indexing completed.")
        self._set_indexing_state(False)
        self.state["index_completed_once"] = True
        save_app_state(self.state)

    def _on_index_failed(self, err: str):
        self.start_index_btn.setEnabled(True)
        self.stop_index_btn.setEnabled(False)
        self._append_log(f"Indexing failed: {err}")
        self._set_indexing_state(False)

    # ── Settings ──────────────────────────────────────────────────────────────

    def _open_settings_dialog(self):
        # Re-use v1 SettingsDialog — import here to avoid circular
        try:
            from app.ui.settings_dialog import SettingsDialog
        except ImportError:
            QMessageBox.information(self, "Settings", "Settings dialog not found. Edit config/settings.py directly.")
            return
        dlg = SettingsDialog(self, on_save=self._apply_settings_changes)
        dlg.show()

    def _apply_settings_changes(self, config: dict, exclude_paths: list):
        self.state.update(config)
        self.state["settings_version"] = int(self.state.get("settings_version", 1)) + 1
        self.state["exclude_paths"] = exclude_paths
        save_app_state(self.state)
        self._load_state_into_ui()
        self._append_log("Settings saved.")

    # ── State helpers ─────────────────────────────────────────────────────────

    def _load_state_into_ui(self):
        self.root_folder = self.state.get("root_folder", "C:/")
        self.root_folder_label.setText(f"Root folder: {self.root_folder}")
        self.exclude_list.clear()
        for path in self.state.get("exclude_paths", []):
            self.exclude_list.addItem(path)

    def _persist_ui_config(self):
        self.state["root_folder"] = self.root_folder
        self.state["exclude_paths"] = [
            self.exclude_list.item(i).text() for i in range(self.exclude_list.count())
        ]
        save_app_state(self.state)

    def _is_model_setup_valid(self) -> bool:
        checks = [
            ("llm_model_path", False), ("embedding_model_path", True),
            ("piper_primary_model", False), ("vosk_model_path", True),
        ]
        for key, is_dir in checks:
            raw = str(self.state.get(key, "")).strip()
            if not raw:
                return False
            p = Path(raw)
            if is_dir and not p.is_dir():
                return False
            if not is_dir and not p.is_file():
                return False
        return True

    def bootstrap_on_start(self):
        try:
            needs_setup = (not self.state.get("consent_given", False)) or \
                          (not self._is_model_setup_valid())
            if needs_setup:
                try:
                    from app.ui.main_v1 import InitialVectorDbDialog
                    dialog = InitialVectorDbDialog(self)
                    if dialog.exec_() != QDialog.Accepted:
                        QApplication.instance().quit()
                        return
                    self.root_folder = dialog.root_folder
                    self.exclude_list.clear()
                    for path in dialog.selected_exclusions():
                        self.exclude_list.addItem(path)
                    self.state.update(dialog.selected_settings())
                    self.state["consent_given"] = True
                    self.state["background_enabled"] = True
                    self._persist_ui_config()
                    enable_background_autostart()
                    launch_background_agent_if_not_running()
                    self._start_indexing()
                except ImportError:
                    pass
            else:
                self._load_state_into_ui()
                if self.state.get("background_enabled", True):
                    launch_background_agent_if_not_running()
                if self.state.get("indexing_in_progress", False):
                    self._start_indexing()
                else:
                    self._set_status("Ready", "ready")

            # Pre-warm orchestrator in background
            QTimer.singleShot(1500, TextPromptWorker.preload)

        except Exception as e:
            import traceback; traceback.print_exc()
            QMessageBox.critical(self, "Startup Error", f"Failed to bootstrap app:\n{str(e)}")

    def closeEvent(self, event):
        self._persist_ui_config()
        self.hide()
        if self.state.get("background_enabled", True) and self._is_wake_word_running():
            launch_background_agent_if_not_running()
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
#  BACKGROUND RUNTIME AGENT
# ─────────────────────────────────────────────────────────────────────────────

class BackgroundRuntimeAgent(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.state = load_app_state()
        self.assistant_worker = None
        self.conversation_window = ConversationWindow(self._end_conversation)
        self.conversation_window.hide()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(2000)
        self._tick_timer.timeout.connect(self._tick)
        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.timeout.connect(self._ensure_assistant_running)
        self._settings_version = int(self.state.get("settings_version", 1))
        app.aboutToQuit.connect(self._shutdown)

        # System tray icon — required for Windows to show the mic privacy indicator
        # in the taskbar when the background agent is using the microphone.
        self.tray = QSystemTrayIcon(self)
        _icon = None
        if getattr(sys, "frozen", False):
            _ico_path = os.path.join(sys._MEIPASS, "resources", "icons", "sentinel_icon.ico")
            if os.path.exists(_ico_path):
                _icon = QIcon(_ico_path)
        if _icon is None or _icon.isNull():
            _icon = app.style().standardIcon(QStyle.SP_MediaVolume)
        self.tray.setIcon(_icon)
        self.tray.setToolTip("SentinelAI – Listening in background")
        tray_menu = QMenu()
        _quit_action = QAction("Quit SentinelAI", tray_menu)
        _quit_action.triggered.connect(app.quit)
        tray_menu.addAction(_quit_action)
        self.tray.setContextMenu(tray_menu)
        self.tray.setVisible(True)
        self.tray.show()

    def start(self):
        if not self.state.get("consent_given", False):
            QTimer.singleShot(100, self.app.quit)
            return
        enable_background_autostart()
        self._tick_timer.start()
        self._tick()

    def _tick(self):
        self.state = load_app_state()
        if not self.state.get("consent_given", False):
            self.app.quit()
            return
        current_version = int(self.state.get("settings_version", 1))
        if current_version != self._settings_version:
            self._restart_assistant()
            self._settings_version = current_version
        self._ensure_assistant_running()

    def _restart_assistant(self):
        if self.assistant_worker and self.assistant_worker.isRunning():
            self.assistant_worker.stop()
            self.assistant_worker.wait(8000)
        self.assistant_worker = None

    def _ensure_assistant_running(self):
        if self.assistant_worker and self.assistant_worker.isRunning():
            return
        self.assistant_worker = AssistantWorker()
        self.assistant_worker.conversation_started.connect(self._on_convo_started)
        self.assistant_worker.conversation_ended.connect(self._on_convo_ended)
        self.assistant_worker.status.connect(lambda s: print(f"[ASSISTANT] {s}"))
        self.assistant_worker.transcript.connect(self.conversation_window._on_user_spoke)
        self.assistant_worker.wave_mode.connect(self.conversation_window.status_indicator.set_mode)
        self.assistant_worker.failed.connect(lambda _: self._restart_timer.start(8000))
        self.assistant_worker.start()

    def _on_hitl_question(self, question: dict):
        """Voice HITL: TTS the question and wait for spoken answer."""
        from app.src.voice_pipeline import speak_text, transcribe_mic
        question_text = question.get("question", "").split("\n")[0][:120]
        speak_text(question_text)
        answer = transcribe_mic()
        if answer:
            self.assistant_worker.answer_hitl(question["id"], answer)

    def _on_convo_started(self):
        self.conversation_window.status_indicator.set_mode("speak")
        self.conversation_window._on_user_spoke("")
        self.conversation_window.show()
        self.conversation_window.raise_()
        self.conversation_window.activateWindow()

    def _on_convo_ended(self):
        self.conversation_window.status_indicator.set_mode("idle")
        self.conversation_window.hide()

    def _end_conversation(self):
        if self.assistant_worker:
            self.assistant_worker.end_conversation_now()
        self.conversation_window.hide()

    def _shutdown(self):
        if self.assistant_worker and self.assistant_worker.isRunning():
            self.assistant_worker.stop()
            self.assistant_worker.wait(8000)


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main():
    import multiprocessing
    import traceback
    from pathlib import Path

    multiprocessing.freeze_support()

    # Mutex guard — only one background instance may run at a time.
    # Must be created before QApplication so it survives for the process lifetime.
    _mutex_handle = None
    if "--background" in sys.argv and os.name == "nt":
        import ctypes
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, "SentinelAI_Background_Mutex")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            sys.exit(0)
    # ── Redirect Logs for Background & Frozen Execution ──
    if "--background" in sys.argv:
        try:
            from app.src.path_utils import data_dir
            log_dir = data_dir().parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = open(log_dir / "wake.log", "a", encoding="utf-8")
            sys.stdout = log_file
            sys.stderr = log_file
        except Exception:
            pass
    elif sys.stdout is None:
        # Frozen UI mode without a console — prevent print() crash
        import io
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()

    multiprocessing.freeze_support()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)

    from app.ui.setup_dialog import check_and_run_setup
    check_and_run_setup()

    if "--background" in sys.argv:
        agent = BackgroundRuntimeAgent(app)
        QTimer.singleShot(0, agent.start)
        sys.exit(app.exec_())

    window = SentinelMainWindow()
    window.show()
    QTimer.singleShot(0, window.bootstrap_on_start)
    sys.exit(app.exec_())

def launch_ui():
    main()

if __name__ == "__main__":
    main()
