"""
hitl_dialog.py — Human-in-the-Loop Selection Dialog

Shown automatically when an agent pauses and needs user input:
  - Product list selection (numbered items with price/rating)
  - Seat map picker (cinema / bus / train)
  - Variant picker (color, size, RAM)
  - Time slot picker
  - Payment confirmation
  - Free-text input (CAPTCHA, address, etc.)
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QPlainTextEdit, QLineEdit,
    QFrame, QScrollArea, QWidget, QButtonGroup, QRadioButton,
    QGridLayout, QSizePolicy, QTextBrowser,
)

# ── Shared style tokens ──────────────────────────────────────────────────────
_DARK_BG   = "#080e18"
_CARD_BG   = "#0d1825"
_BORDER    = "#1c2f47"
_ACCENT    = "#4f9de0"
_TEXT_PRI  = "#d4e8ff"
_TEXT_SEC  = "#4a7aaa"
_SUCCESS   = "#00d28c"
_DANGER    = "#dc465a"

_BASE_STYLE = f"""
QDialog, QWidget {{
    background: {_DARK_BG};
    color: {_TEXT_PRI};
    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
    font-size: 10pt;
}}
QLabel {{ color: {_TEXT_PRI}; background: transparent; }}
QLabel#title {{ font-size: 12pt; font-weight: 700; color: #e8f4ff; }}
QLabel#subtitle {{ font-size: 9pt; color: {_TEXT_SEC}; }}
QLabel#question {{ font-size: 10.5pt; color: {_TEXT_PRI}; line-height: 1.5; }}
QPushButton {{
    background: #111e2e; border: 1px solid {_BORDER};
    border-radius: 8px; padding: 8px 18px;
    color: #9fc5e8; font-weight: 500;
}}
QPushButton:hover {{ background: #172843; border-color: #2a5080; color: #c8e0ff; }}
QPushButton#primary {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1b4e8a,stop:1 #1464b4);
    border: none; color: #e8f4ff; font-weight: 700; border-radius: 10px;
    padding: 10px 24px;
}}
QPushButton#primary:hover {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #225fa8,stop:1 #1a76d4);
}}
QPushButton#confirm {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0a6646,stop:1 #0a8058);
    border: none; color: #c0fff0; font-weight: 700; border-radius: 10px;
    padding: 10px 24px;
}}
QPushButton#danger {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #6e1628,stop:1 #922040);
    border: none; color: #ffd8df; font-weight: 700; border-radius: 10px;
    padding: 10px 24px;
}}
QListWidget {{
    background: {_CARD_BG}; border: 1px solid {_BORDER};
    border-radius: 8px; color: {_TEXT_PRI}; padding: 4px;
}}
QListWidget::item {{ padding: 8px 12px; border-radius: 4px; }}
QListWidget::item:selected {{ background: #1a3a60; color: #c8e4ff; }}
QListWidget::item:hover {{ background: #122030; }}
QLineEdit, QPlainTextEdit {{
    background: {_CARD_BG}; border: 1px solid {_BORDER};
    border-radius: 8px; padding: 8px 12px; color: {_TEXT_PRI};
}}
QLineEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid #2e6aac; background: #0f1e2e;
}}
QScrollBar:vertical {{
    background: {_DARK_BG}; width: 6px; border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: #1c3050; border-radius: 3px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


# ─────────────────────────────────────────────────────────────────────────────
#  BASE HITL DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class HITLDialog(QDialog):
    """Base class for all HITL dialogs."""

    answered = pyqtSignal(str)   # emits user's answer string

    def __init__(self, question: dict, parent=None):
        super().__init__(parent)
        self.question = question
        self.setModal(True)
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(_BASE_STYLE)
        self.resize(580, 400)
        self._answer: str = ""

    def _header(self, layout: QVBoxLayout, icon: str, title: str, subtitle: str = ""):
        row = QHBoxLayout()
        ico = QLabel(icon)
        ico.setStyleSheet("font-size: 20pt; background: transparent;")
        row.addWidget(ico)
        col = QVBoxLayout()
        t = QLabel(title)
        t.setObjectName("title")
        col.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("subtitle")
            col.addWidget(s)
        row.addLayout(col)
        row.addStretch()
        layout.addLayout(row)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {_BORDER};")
        layout.addWidget(sep)

    def _footer_buttons(self, layout: QVBoxLayout, confirm_text: str = "Confirm",
                        cancel_text: str = "Cancel"):
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {_BORDER};")
        layout.addWidget(sep)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(cancel_text)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        confirm = QPushButton(confirm_text)
        confirm.setObjectName("primary")
        confirm.clicked.connect(self._on_confirm)
        row.addWidget(confirm)
        layout.addLayout(row)

    def _on_confirm(self):
        self.answered.emit(self._answer)
        self.accept()

    def _emit_and_close(self, answer: str):
        self._answer = answer
        self.answered.emit(answer)
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
#  1. PRODUCT LIST SELECTION
# ─────────────────────────────────────────────────────────────────────────────

class ProductListDialog(HITLDialog):
    """Shows a numbered list of products for the user to pick from."""

    def __init__(self, question: dict, parent=None):
        super().__init__(question, parent)
        self.setWindowTitle("Choose a Product — Sentinel")
        self.resize(640, 480)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._header(layout, "🛒", "Select a Product",
                     "Tap an item then click Confirm")

        # Question text
        q_label = QLabel(self.question.get("question", "Which product?"))
        q_label.setObjectName("question")
        q_label.setWordWrap(True)
        layout.addWidget(q_label)

        # List
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setStyleSheet(self.list_widget.styleSheet() +
            "QListWidget { alternate-background-color: #0a1420; }")
        for opt in self.question.get("options", []):
            item = QListWidgetItem(opt)
            item.setFont(QFont("Segoe UI Variable Text", 10))
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
            self._answer = self.list_widget.item(0).text()
        self.list_widget.currentTextChanged.connect(lambda t: setattr(self, '_answer', t))
        self.list_widget.itemDoubleClicked.connect(lambda i: self._emit_and_close(i.text()))
        layout.addWidget(self.list_widget, stretch=1)

        self._footer_buttons(layout, "Select This")


# ─────────────────────────────────────────────────────────────────────────────
#  2. VARIANT PICKER (color / size / RAM)
# ─────────────────────────────────────────────────────────────────────────────

class VariantPickerDialog(HITLDialog):
    """Chip-style variant selector for color, size, storage, etc."""

    def __init__(self, question: dict, parent=None):
        super().__init__(question, parent)
        self.setWindowTitle("Choose Variant — Sentinel")
        self.resize(520, 340)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._header(layout, "🎨", "Select Variant")

        q_label = QLabel(self.question.get("question", "Choose an option:"))
        q_label.setObjectName("question")
        q_label.setWordWrap(True)
        layout.addWidget(q_label)

        # Chip grid
        chip_area = QWidget()
        chip_grid = QGridLayout(chip_area)
        chip_grid.setSpacing(8)
        options = self.question.get("options", [])
        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)

        for i, opt in enumerate(options):
            btn = QPushButton(opt)
            btn.setCheckable(True)
            btn.setStyleSheet(f"""
                QPushButton {{ background: #0d1825; border: 1.5px solid {_BORDER};
                    border-radius: 20px; padding: 6px 16px; color: {_TEXT_PRI}; font-size: 9.5pt; }}
                QPushButton:checked {{ background: #1464b4; border-color: #4f9de0; color: #e8f4ff; font-weight: 700; }}
                QPushButton:hover {{ border-color: {_ACCENT}; }}
            """)
            if i == 0:
                btn.setChecked(True)
                self._answer = opt
            btn.clicked.connect(lambda checked, o=opt: setattr(self, '_answer', o))
            self._btn_group.addButton(btn, i)
            chip_grid.addWidget(btn, i // 4, i % 4)

        layout.addWidget(chip_area)
        layout.addStretch()
        self._footer_buttons(layout, "Confirm")


# ─────────────────────────────────────────────────────────────────────────────
#  3. SEAT MAP DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class SeatMapDialog(HITLDialog):
    """Displays a cinema/train/bus seat map and lets user pick seats."""

    def __init__(self, question: dict, parent=None):
        super().__init__(question, parent)
        self.setWindowTitle("Choose Seats — Sentinel")
        self.resize(680, 520)
        self._selected_seats: list[str] = []
        self._seat_buttons: dict[str, QPushButton] = {}
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        meta = self.question.get("metadata", {})
        num_seats = meta.get("num_seats", 1)
        self._num_seats = num_seats

        self._header(layout, "🎬", "Select Your Seats",
                     f"Choose {num_seats} seat(s) — green = available, red = booked")

        # Seat grid from question text (parse ✓/✗ grid)
        grid_text = self.question.get("question", "")
        seat_area = self._build_seat_grid(grid_text)
        layout.addWidget(seat_area, stretch=1)

        # Selected seats label
        self.selected_label = QLabel("Selected: none")
        self.selected_label.setStyleSheet(f"color: {_SUCCESS}; font-weight: 600;")
        layout.addWidget(self.selected_label)

        self._footer_buttons(layout, "Confirm Seats")

    def _build_seat_grid(self, grid_text: str) -> QScrollArea:
        """Parse text grid like 'A: [✓1][✗2][✓3]' and create clickable buttons."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background: {_CARD_BG}; border: 1px solid {_BORDER}; border-radius: 8px; }}")

        container = QWidget()
        grid = QGridLayout(container)
        grid.setSpacing(4)

        import re
        row_idx = 0
        for line in grid_text.split("\n"):
            line = line.strip()
            if not line or ":" not in line or not any(c in line for c in ["✓", "✗", "[", "]"]):
                continue
            # Parse row label
            row_label_match = re.match(r"([A-Z])\s*:", line)
            if not row_label_match:
                continue
            row_label = row_label_match.group(1)

            lbl = QLabel(row_label)
            lbl.setStyleSheet(f"color: {_TEXT_SEC}; font-weight: 700; font-size: 11pt;")
            lbl.setFixedWidth(24)
            grid.addWidget(lbl, row_idx, 0)

            # Parse seats: [✓N] or [✗N]
            seats = re.findall(r"\[([✓✗])(\w+)\]", line)
            for col_idx, (status, seat_num) in enumerate(seats):
                seat_id = f"{row_label}{seat_num}"
                available = status == "✓"

                btn = QPushButton(seat_num)
                btn.setFixedSize(36, 28)
                btn.setEnabled(available)

                if available:
                    btn.setStyleSheet(f"""
                        QPushButton {{ background: #0a3020; border: 1px solid #1a6040;
                            border-radius: 4px; color: {_SUCCESS}; font-size: 8pt; }}
                        QPushButton:hover {{ background: #0d4030; }}
                        QPushButton:checked {{ background: #1464b4; border-color: {_ACCENT};
                            color: white; font-weight: 700; }}
                    """)
                    btn.setCheckable(True)
                    btn.clicked.connect(lambda checked, sid=seat_id, b=btn: self._toggle_seat(sid, b, checked))
                else:
                    btn.setStyleSheet(f"""
                        QPushButton {{ background: #2a0a0a; border: 1px solid #4a1a1a;
                            border-radius: 4px; color: {_DANGER}; font-size: 8pt; }}
                    """)

                self._seat_buttons[seat_id] = btn
                grid.addWidget(btn, row_idx, col_idx + 1)

            row_idx += 1

        scroll.setWidget(container)
        return scroll

    def _toggle_seat(self, seat_id: str, btn: QPushButton, checked: bool):
        if checked:
            if len(self._selected_seats) >= self._num_seats:
                # Deselect oldest
                oldest = self._selected_seats.pop(0)
                if oldest in self._seat_buttons:
                    self._seat_buttons[oldest].setChecked(False)
            self._selected_seats.append(seat_id)
        else:
            if seat_id in self._selected_seats:
                self._selected_seats.remove(seat_id)

        self._answer = ", ".join(self._selected_seats)
        self.selected_label.setText(
            f"Selected: {self._answer}" if self._answer else "Selected: none"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  4. TIME SLOT DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class TimeSlotDialog(HITLDialog):
    def __init__(self, question: dict, parent=None):
        super().__init__(question, parent)
        self.setWindowTitle("Choose Time Slot — Sentinel")
        self.resize(520, 360)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        self._header(layout, "🕐", "Select Time Slot")

        q_label = QLabel(self.question.get("question", "Choose a time:"))
        q_label.setObjectName("question")
        q_label.setWordWrap(True)
        layout.addWidget(q_label)

        self.list_widget = QListWidget()
        for opt in self.question.get("options", []):
            self.list_widget.addItem(opt)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
            self._answer = self.list_widget.item(0).text()
        self.list_widget.currentTextChanged.connect(lambda t: setattr(self, '_answer', t))
        self.list_widget.itemDoubleClicked.connect(lambda i: self._emit_and_close(i.text()))
        layout.addWidget(self.list_widget, stretch=1)
        self._footer_buttons(layout, "Book This Slot")


# ─────────────────────────────────────────────────────────────────────────────
#  5. PAYMENT CONFIRMATION
# ─────────────────────────────────────────────────────────────────────────────

class PaymentConfirmDialog(HITLDialog):
    def __init__(self, question: dict, parent=None):
        super().__init__(question, parent)
        self.setWindowTitle("Confirm Payment — Sentinel")
        self.resize(560, 420)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        self._header(layout, "💳", "Confirm Your Order",
                     "Review the order summary before proceeding to payment")

        # Order summary
        summary_box = QTextBrowser()
        summary_box.setStyleSheet(f"""
            QTextBrowser {{
                background: {_CARD_BG}; border: 1px solid {_BORDER};
                border-radius: 8px; padding: 12px; color: {_TEXT_PRI};
                font-size: 10pt; line-height: 1.6;
            }}
        """)
        summary_text = self.question.get("question", "").replace("ORDER SUMMARY:\n", "")
        summary_box.setPlainText(summary_text)
        layout.addWidget(summary_box, stretch=1)

        # Warning
        warn = QLabel("⚠  This will proceed to the payment page. Payment will NOT be submitted automatically.")
        warn.setStyleSheet(f"color: #f0c040; font-size: 9pt; padding: 4px;")
        warn.setWordWrap(True)
        layout.addWidget(warn)

        # Buttons
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {_BORDER};")
        layout.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel Order")
        cancel_btn.setObjectName("danger")
        cancel_btn.clicked.connect(lambda: self._emit_and_close("no"))
        btn_row.addWidget(cancel_btn)
        confirm_btn = QPushButton("Yes, Proceed to Payment")
        confirm_btn.setObjectName("confirm")
        confirm_btn.clicked.connect(lambda: self._emit_and_close("yes"))
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)


# ─────────────────────────────────────────────────────────────────────────────
#  6. FREE TEXT INPUT (CAPTCHA, address, login, etc.)
# ─────────────────────────────────────────────────────────────────────────────

class FreeTextDialog(HITLDialog):
    def __init__(self, question: dict, parent=None):
        super().__init__(question, parent)
        self.setWindowTitle("Input Needed — Sentinel")
        self.resize(520, 300)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        self._header(layout, "✏️", "Your Input Needed")

        q_label = QLabel(self.question.get("question", "Please provide input:"))
        q_label.setObjectName("question")
        q_label.setWordWrap(True)
        layout.addWidget(q_label)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type your answer here...")
        self.input.setFixedHeight(44)
        self.input.textChanged.connect(lambda t: setattr(self, '_answer', t))
        self.input.returnPressed.connect(self._on_confirm)
        layout.addWidget(self.input)
        layout.addStretch()
        self._footer_buttons(layout, "Submit")


# ─────────────────────────────────────────────────────────────────────────────
#  DIALOG FACTORY
# ─────────────────────────────────────────────────────────────────────────────

def make_hitl_dialog(question: dict, parent=None) -> HITLDialog:
    """Create the appropriate HITL dialog based on selection_type."""
    stype = question.get("selection_type", "text")

    if stype == "list":
        # Use seat map dialog if there's grid data, else product list
        meta = question.get("metadata", {})
        if meta.get("grid"):
            return SeatMapDialog(question, parent)
        # Check if options look like time slots
        opts = question.get("options", [])
        if opts and any(c in " ".join(opts) for c in ["AM", "PM", "hour", "window", "delivery"]):
            return TimeSlotDialog(question, parent)
        return ProductListDialog(question, parent)

    elif stype == "seat_map":
        return SeatMapDialog(question, parent)

    elif stype == "variant":
        return VariantPickerDialog(question, parent)

    elif stype == "confirm":
        return PaymentConfirmDialog(question, parent)

    else:
        # Default: free text
        if question.get("options"):
            return ProductListDialog(question, parent)
        return FreeTextDialog(question, parent)
