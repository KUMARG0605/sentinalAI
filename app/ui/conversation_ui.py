#!/usr/bin/env python3
"""
Conversation UI for SentinelAI.
Lightweight overlay — shows only:
  - Current listening/speaking/thinking state
  - Live user voice transcription (what the user just said)
No response text, no chat history, no sources.
Wake word detection: Vosk (via wake_word_worker)
Query STT: AssemblyAI (via stt.transcribe_mic)
"""
import sys
import threading
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread, QPoint
from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QWidget, QDesktopWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.src.stt import transcribe_mic
from app.src.rag import ask, load_essentials, stop_llama_server
from app.src.voice_pipeline import speak_text, is_conversation_ending


# ─────────────────────────────────────────────────────────────────────────────
#  STATUS INDICATOR  (pulsing dot + mode label)
# ─────────────────────────────────────────────────────────────────────────────

_MODE_META = {
    "idle":    {"dot": "#1e3a5c", "text": "#2a4870", "label": "Idle"},
    "wake":    {"dot": "#f0c040", "text": "#c8a030", "label": "Listening for wake word…"},
    "listen":  {"dot": "#4fc8f0", "text": "#4fb8e0", "label": "Listening…"},
    "process": {"dot": "#a070f0", "text": "#8860d8", "label": "Processing…"},
    "speak":   {"dot": "#00d28c", "text": "#00b87a", "label": "Speaking…"},
}


class StatusIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "idle"
        self._pulse_on = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._dot = QLabel("●")
        self._dot.setFixedWidth(20)
        self._dot.setAlignment(Qt.AlignCenter)
        self._dot.setStyleSheet("background: transparent;")

        self._lbl = QLabel("Idle")
        self._lbl.setStyleSheet(
            "font-size: 11pt; font-weight: 700; background: transparent;"
        )

        layout.addWidget(self._dot)
        layout.addWidget(self._lbl)
        layout.addStretch()

        self._timer = QTimer(self)
        self._timer.setInterval(550)
        self._timer.timeout.connect(self._pulse)
        self._timer.start()
        self.set_mode("idle")

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        meta = _MODE_META.get(mode, _MODE_META["idle"])
        self._dot.setStyleSheet(
            f"font-size: 15pt; color: {meta['dot']}; background: transparent;"
        )
        self._lbl.setStyleSheet(
            f"font-size: 11pt; font-weight: 700; color: {meta['text']}; background: transparent;"
        )
        self._lbl.setText(meta["label"])
        self._pulse_on = True

    def _pulse(self):
        if self._mode == "idle":
            return
        self._pulse_on = not self._pulse_on
        meta = _MODE_META.get(self._mode, _MODE_META["idle"])
        color = meta["dot"] if self._pulse_on else "#0f1e30"
        self._dot.setStyleSheet(
            f"font-size: 15pt; color: {color}; background: transparent;"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  CONVERSATION WORKER
#  - Vosk handles wake word (already done upstream, this worker is started
#    AFTER wake word fires)
#  - AssemblyAI (transcribe_mic) handles query STT
#  - Only emits the user's spoken text to the UI, NOT the AI response text
# ─────────────────────────────────────────────────────────────────────────────

class ConversationWorker(QThread):
    mode_changed       = pyqtSignal(str)
    user_spoke         = pyqtSignal(str)
    status_msg         = pyqtSignal(str)
    conversation_ended = pyqtSignal()

    def __init__(self, skip_greet: bool = False):
        super().__init__()
        self._running    = True
        self._stop       = threading.Event()
        self._skip_greet = skip_greet   # True when restarted after End button
        self.retriever   = None
        self.llm         = None
        self.server_proc = None

    def stop(self):
        self._running = False
        self._stop.set()

    def end_conversation_now(self):
        """Stop immediately — interrupts the STT blocking call too."""
        self._running = False
        self._stop.set()

    def run(self):
        try:
            if not self.retriever or not self.llm:
                self.mode_changed.emit("process")
                self.status_msg.emit("Loading models…")
                self.retriever, self.llm, self.server_proc = load_essentials()

            if not self._skip_greet and not self._stop.is_set():
                self.mode_changed.emit("speak")
                speak_text("How can I help you?")

            while self._running and not self._stop.is_set():
                # ── Listen ────────────────────────────────────────────────────
                self.mode_changed.emit("listen")
                self.user_spoke.emit("")

                query = transcribe_mic(
                    on_partial=lambda t: self.user_spoke.emit(t)
                )

                if self._stop.is_set():
                    break

                if not query:
                    continue

                self.user_spoke.emit(query)

                if is_conversation_ending(query, llm=self.llm):
                    self.mode_changed.emit("speak")
                    speak_text("Alright. Going back to wake mode.")
                    break

                # ── Process ───────────────────────────────────────────────────
                self.mode_changed.emit("process")
                try:
                    ask(query, self.retriever, self.llm, abort_event=self._stop)
                except Exception as e:
                    self.status_msg.emit(f"Error: {e}")
                    continue

                if self._stop.is_set():
                    break

                # ── Speak ─────────────────────────────────────────────────────
                self.mode_changed.emit("speak")
                speak_text("Anything else?")

        except Exception as e:
            self.status_msg.emit(f"Error: {e}")
        finally:
            self.mode_changed.emit("idle")
            self.conversation_ended.emit()

    def __del__(self):
        if self.server_proc:
            stop_llama_server(self.server_proc)


# ─────────────────────────────────────────────────────────────────────────────
#  CONVERSATION WINDOW
#  Compact overlay: status indicator + last spoken text + End button
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
#  DRAGGABLE MIXIN  — lets the frameless window be dragged anywhere
# ─────────────────────────────────────────────────────────────────────────────

class _DraggableMixin:
    """
    Mix into a QDialog/QWidget to allow dragging a frameless window.
    Drag starts on left-click anywhere except interactive child widgets.
    """
    _drag_pos: QPoint | None = None

    def _is_interactive(self, widget) -> bool:
        """Return True for buttons and other widgets that should NOT start a drag."""
        return isinstance(widget, QPushButton)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.pos())
            if not self._is_interactive(child):
                self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


class ConversationWindow(_DraggableMixin, QDialog):
    def __init__(self, on_close_callback=None):
        super().__init__()
        self.on_close_callback   = on_close_callback
        self.conversation_worker = None
        # Cache loaded models so restarts don't re-load them
        self._retriever   = None
        self._llm         = None
        self._server_proc = None
        self._build_ui()
        self._apply_theme()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("SentinelAI — Voice")
        self.setFixedSize(440, 80)          # slim horizontal bar at bottom
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
            | Qt.Tool               # keeps it off the taskbar
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        card = QWidget(self)
        card.setObjectName("card")
        card.setCursor(Qt.SizeAllCursor)   # shows move cursor → user knows it's draggable
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        # Single horizontal row: grip | status dot | transcript | End button
        row = QHBoxLayout(card)
        row.setContentsMargins(10, 10, 14, 10)
        row.setSpacing(10)

        # Drag grip indicator (⠿ braille pattern looks like a grip)
        grip = QLabel("⠿")
        grip.setFixedWidth(14)
        grip.setStyleSheet("font-size: 11pt; color: #1a3a5a; background: transparent;")
        grip.setToolTip("Drag to move")
        row.addWidget(grip)

        # Status indicator (dot + mode label)
        self.status_indicator = StatusIndicator()
        self.status_indicator.setFixedWidth(160)
        row.addWidget(self.status_indicator)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #0f1e30;")
        row.addWidget(sep)

        # Live transcript label
        self.transcript_label = QLabel("Say something…")
        self.transcript_label.setWordWrap(False)
        self.transcript_label.setSizePolicy(
            self.transcript_label.sizePolicy().horizontalPolicy(),
            self.transcript_label.sizePolicy().verticalPolicy()
        )
        self.transcript_label.setStyleSheet(
            "font-size: 9.5pt; color: #4a7090; font-style: italic;"
            " background: transparent;"
        )
        row.addWidget(self.transcript_label, stretch=1)

        # End Session button
        self.end_btn = QPushButton("■  End")
        self.end_btn.setObjectName("endBtn")
        self.end_btn.setFixedSize(72, 30)
        self.end_btn.clicked.connect(self._end_clicked)
        row.addWidget(self.end_btn)

    def _apply_theme(self):
        self.setStyleSheet("""
            QWidget#card {
                background: rgba(8, 14, 24, 220);
                border: 1px solid #1a3050;
                border-radius: 12px;
            }
            QPushButton#endBtn {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #5a1020, stop:1 #781836);
                border: none; border-radius: 7px;
                color: #ffccd4; font-weight: 700; font-size: 8.5pt;
            }
            QPushButton#endBtn:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #741428, stop:1 #9c2044);
            }
        """)

    # ── Positioning ───────────────────────────────────────────────────────────

    def _move_to_bottom_center(self):
        """Place the bar at the bottom-center of the primary screen, just above the taskbar."""
        screen = QDesktopWidget().availableGeometry()   # excludes taskbar
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.y() + screen.height() - self.height() - 8   # 8px gap from taskbar
        self.move(x, y)

    # ── Worker lifecycle ──────────────────────────────────────────────────────

    def start_conversation(self, skip_greet: bool = False):
        """Start (or restart) the conversation worker."""
        if self.conversation_worker and self.conversation_worker.isRunning():
            return

        worker = ConversationWorker(skip_greet=skip_greet)

        # Re-use already-loaded models so restart is instant
        worker.retriever   = self._retriever
        worker.llm         = self._llm
        worker.server_proc = self._server_proc

        worker.mode_changed.connect(self.status_indicator.set_mode)
        worker.user_spoke.connect(self._on_user_spoke)
        worker.status_msg.connect(self._on_status_msg)
        worker.conversation_ended.connect(self._on_ended)

        self.conversation_worker = worker
        worker.start()

    def end_conversation(self):
        if self.conversation_worker:
            self.conversation_worker.end_conversation_now()

    # ── Button handler ────────────────────────────────────────────────────────

    def _end_clicked(self):
        """
        Stop whatever the agent is doing RIGHT NOW and immediately
        start listening for the next question — do NOT close the window.
        """
        # 1. Kill current worker instantly
        if self.conversation_worker and self.conversation_worker.isRunning():
            self.conversation_worker.end_conversation_now()
            # Save models before the worker is garbage-collected
            self._retriever   = self.conversation_worker.retriever
            self._llm         = self.conversation_worker.llm
            self._server_proc = self.conversation_worker.server_proc
            self.conversation_worker.wait(2000)   # brief wait for thread exit

        # 2. Update UI immediately to show we're ready
        self.status_indicator.set_mode("listen")
        self._on_user_spoke("")

        # 3. Restart listening at once — skip the greeting
        QTimer.singleShot(200, lambda: self.start_conversation(skip_greet=True))

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_user_spoke(self, text: str):
        if text:
            # Truncate long text so the bar stays compact
            display = text if len(text) <= 60 else text[:57] + "…"
            self.transcript_label.setText(f'"{display}"')
            self.transcript_label.setStyleSheet(
                "font-size: 9.5pt; color: #90c4e8; font-style: italic;"
                " background: transparent;"
            )
        else:
            self.transcript_label.setText("Say something…")
            self.transcript_label.setStyleSheet(
                "font-size: 9.5pt; color: #4a7090; font-style: italic;"
                " background: transparent;"
            )

    def _on_status_msg(self, msg: str):
        if msg.lower().startswith("error"):
            self.transcript_label.setText(msg[:60])
            self.transcript_label.setStyleSheet(
                "font-size: 9pt; color: #dc465a; background: transparent;"
            )

    def _on_ended(self):
        # Cache models before the worker dies
        if self.conversation_worker:
            self._retriever   = self.conversation_worker.retriever
            self._llm         = self.conversation_worker.llm
            self._server_proc = self.conversation_worker.server_proc
        self.status_indicator.set_mode("idle")
        if self.on_close_callback:
            self.on_close_callback()

    # ── Window events ─────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self.end_conversation()
        event.ignore()
        self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        self._move_to_bottom_center()
        QTimer.singleShot(300, self.start_conversation)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = ConversationWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
