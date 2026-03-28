"""
selection_context.py — Human-in-the-loop selection management.

Manages all the points where the agent pauses and asks the user to pick:
  - Product from a filtered results list
  - Variant (color, size, RAM, etc.)
  - Cinema/bus/train seats
  - Time slot / delivery window
  - Add-ons and extras
  - Final payment confirmation

The SelectionContext travels with each task via the blackboard.
Agents write pending selections; the UI/voice layer fills them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.src.blackboard import Blackboard


# ─────────────────────────────────────────────────────────────────────────────
#  DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProductItem:
    index: int
    title: str
    price: str
    rating: str = ""
    delivery: str = ""
    platform: str = ""
    url: str = ""
    extra: dict = field(default_factory=dict)

    def display_line(self) -> str:
        parts = [f"{self.index}. {self.title}", f"₹{self.price}"]
        if self.rating:  parts.append(f"{self.rating}★")
        if self.delivery: parts.append(self.delivery)
        if self.platform: parts.append(f"[{self.platform}]")
        return "  ".join(parts)


@dataclass
class SeatInfo:
    row: str
    col: str | int
    status: str   # available | booked | selected | premium
    price: str = ""
    seat_type: str = ""  # regular | premium | recliner

    @property
    def id(self) -> str:
        return f"{self.row}{self.col}"

    def symbol(self) -> str:
        return {"available": "✓", "booked": "✗", "selected": "●", "premium": "P"}.get(self.status, "?")


@dataclass
class TimeSlot:
    index: int
    label: str          # "10:30 AM" or "2-hour window: 2PM–4PM"
    available: bool = True
    price_extra: str = ""
    duration: str = ""


@dataclass
class VariantOption:
    attribute: str      # "color" | "size" | "RAM" | "storage"
    options: list[str]
    selected: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
#  SELECTION CONTEXT
# ─────────────────────────────────────────────────────────────────────────────

class SelectionContext:
    """
    Manages the full selection flow for one task.
    Wraps Blackboard.ask_human() with richer presentation helpers.
    """

    def __init__(self, blackboard: Blackboard, task_id: str):
        self.bb = blackboard
        self.task_id = task_id
        self._selected: dict[str, Any] = {}

    # ── Product list selection ─────────────────────────────────────────────

    def present_product_list(
        self,
        items: list[ProductItem],
        prompt: str = "Which product would you like?",
    ) -> ProductItem:
        """
        Display a numbered product list and wait for user to pick one.
        Returns the selected ProductItem.
        """
        lines = [prompt]
        for item in items:
            lines.append(item.display_line())

        question = "\n".join(lines)
        options = [item.display_line() for item in items]
        options.append("None of these — show more results")

        question_id = f"{self.task_id}_product_{uuid.uuid4().hex[:6]}"
        answer = self.bb.ask_human(
            question_id=question_id,
            question=question,
            selection_type="list",
            options=options,
        )

        # Parse answer: accept index number, partial name match, or "none"
        selected = self._match_selection(answer, items)
        self._selected["product"] = selected
        self.bb.set("selected_product", selected)
        return selected

    def _match_selection(self, answer: str, items: list[ProductItem]) -> ProductItem:
        """Match user's text answer to a ProductItem."""
        ans = answer.strip().lower()
        # Try direct index
        if ans.isdigit():
            idx = int(ans)
            for item in items:
                if item.index == idx:
                    return item
        # Try name match
        for item in items:
            if ans in item.title.lower():
                return item
        # Default to first
        return items[0]

    # ── Variant selection ──────────────────────────────────────────────────

    def pick_variant(self, attribute: str, options: list[str]) -> str:
        """Ask user to pick one variant (color, size, RAM, etc.)."""
        question_id = f"{self.task_id}_variant_{attribute}_{uuid.uuid4().hex[:6]}"
        question = f"Please choose a {attribute}:"

        answer = self.bb.ask_human(
            question_id=question_id,
            question=question,
            selection_type="variant",
            options=options,
        )

        # Match answer to available options
        ans_lower = answer.strip().lower()
        for opt in options:
            if ans_lower in opt.lower() or opt.lower() in ans_lower:
                self._selected[attribute] = opt
                self.bb.set(f"variant_{attribute}", opt)
                return opt
        # Fallback
        self._selected[attribute] = answer
        self.bb.set(f"variant_{attribute}", answer)
        return answer

    # ── Seat map selection ─────────────────────────────────────────────────

    def present_seat_map(
        self,
        seats: list[SeatInfo],
        num_seats: int = 1,
        prompt: str = "Please choose your seats:",
    ) -> list[SeatInfo]:
        """
        Render seat grid as text and ask user to pick seats.
        Returns list of selected SeatInfo objects.

        Grid format:
            A: [✓][✓][✗][✓][✓]  [✓][✓][✓]  (P=Premium)
            B: [✓][✗][✗][✓][✓]  [P][P][✓]
        """
        # Group by row
        rows: dict[str, list[SeatInfo]] = {}
        for seat in seats:
            rows.setdefault(seat.row, []).append(seat)

        grid_lines = [prompt, f"Select {num_seats} seat(s). Available ✓  Booked ✗  Premium P", ""]
        for row_label in sorted(rows.keys()):
            row_seats = sorted(rows[row_label], key=lambda s: str(s.col))
            symbols = "".join(f"[{s.symbol()}]" for s in row_seats)
            grid_lines.append(f"  {row_label}: {symbols}")
        grid_lines.append("\nEnter seat IDs like: A4, A5 or B3")

        question = "\n".join(grid_lines)
        question_id = f"{self.task_id}_seats_{uuid.uuid4().hex[:6]}"

        answer = self.bb.ask_human(
            question_id=question_id,
            question=question,
            selection_type="seat_map",
            options=[],
            metadata={"grid": grid_lines, "num_seats": num_seats},
        )

        # Parse answer: "A4, A5" or "B3" or "front row"
        selected_seats = self._parse_seat_answer(answer, seats, num_seats)
        self._selected["seats"] = [s.id for s in selected_seats]
        self.bb.set("selected_seats", [s.id for s in selected_seats])
        return selected_seats

    def _parse_seat_answer(
        self,
        answer: str,
        seats: list[SeatInfo],
        num_seats: int,
    ) -> list[SeatInfo]:
        """Parse 'A4, A5' style seat selections."""
        import re
        # Extract seat IDs like A4, B12, C3
        ids = re.findall(r"[A-Za-z]\s*\d+", answer.upper().replace(" ", ""))
        ids = [re.sub(r"\s+", "", sid) for sid in ids]

        seat_map = {s.id.upper(): s for s in seats}
        selected = []
        for sid in ids:
            if sid in seat_map and seat_map[sid].status == "available":
                selected.append(seat_map[sid])
        # If nothing matched, return first N available seats
        if not selected:
            selected = [s for s in seats if s.status == "available"][:num_seats]
        return selected[:num_seats]

    # ── Time slot selection ────────────────────────────────────────────────

    def pick_time_slot(self, slots: list[TimeSlot], prompt: str = "Choose a time slot:") -> TimeSlot:
        """Ask user to pick a delivery window or appointment time."""
        options = []
        for slot in slots:
            label = slot.label
            if slot.price_extra: label += f" (+{slot.price_extra})"
            if not slot.available: label += " [Full]"
            options.append(label)

        question_id = f"{self.task_id}_slot_{uuid.uuid4().hex[:6]}"
        answer = self.bb.ask_human(
            question_id=question_id,
            question=prompt,
            selection_type="list",
            options=options,
        )

        ans_lower = answer.strip().lower()
        for i, slot in enumerate(slots):
            if str(i + 1) == answer.strip() or ans_lower in slot.label.lower():
                self._selected["time_slot"] = slot.label
                self.bb.set("selected_slot", slot.label)
                return slot
        # Fallback to first available
        for slot in slots:
            if slot.available:
                self._selected["time_slot"] = slot.label
                self.bb.set("selected_slot", slot.label)
                return slot
        return slots[0]

    # ── Add-ons / extras ──────────────────────────────────────────────────

    def pick_addons(
        self,
        options: list[str],
        prompt: str = "Would you like any add-ons? (say 'none' to skip)",
    ) -> list[str]:
        """Multi-select add-ons (toppings, insurance, extended warranty, etc.)."""
        question_id = f"{self.task_id}_addons_{uuid.uuid4().hex[:6]}"
        answer = self.bb.ask_human(
            question_id=question_id,
            question=f"{prompt}\nOptions: {', '.join(options)}",
            selection_type="list",
            options=options + ["None"],
        )

        if answer.strip().lower() in ("none", "no", "skip", "0"):
            self.bb.set("selected_addons", [])
            return []

        # Match any mentioned options
        selected = [opt for opt in options if opt.lower() in answer.lower()]
        self.bb.set("selected_addons", selected)
        return selected

    # ── Payment confirmation ───────────────────────────────────────────────

    def confirm_payment(self, order_summary: str) -> bool:
        """
        Show full order summary and ask for explicit payment confirmation.
        Returns True only if user explicitly confirms.
        """
        question = (
            f"ORDER SUMMARY:\n{order_summary}\n\n"
            "Do you want to proceed with payment? (yes / no)"
        )
        question_id = f"{self.task_id}_payment_confirm_{uuid.uuid4().hex[:6]}"

        answer = self.bb.ask_human(
            question_id=question_id,
            question=question,
            selection_type="confirm",
            options=["Yes, proceed", "No, cancel"],
        )

        confirmed = answer.strip().lower() in (
            "yes", "y", "confirm", "proceed", "go ahead", "ok", "okay", "sure"
        )
        self.bb.set("payment_confirmed", confirmed)
        return confirmed

    # ── Generic question ───────────────────────────────────────────────────

    def ask(self, question: str, options: list[str] = None) -> str:
        """Generic HITL question."""
        question_id = f"{self.task_id}_q_{uuid.uuid4().hex[:6]}"
        return self.bb.ask_human(
            question_id=question_id,
            question=question,
            selection_type="list" if options else "text",
            options=options or [],
        )

    # ── Summary ───────────────────────────────────────────────────────────

    def get_selections(self) -> dict:
        return dict(self._selected)
