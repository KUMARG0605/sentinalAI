"""
comms_agent.py — Communications Agent

Sends messages via WhatsApp Desktop, Telegram Desktop, and Email.
Uses the AppRegistry to find real exe paths — works for all install types:
  - Microsoft Store (WindowsApps)
  - AppData / Roaming installs
  - PATH-based installs
  - Custom install locations

No hardcoded paths. No "WhatsApp.exe not found" errors.
"""

from __future__ import annotations

import os
import re
import subprocess
import time

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool


# ─────────────────────────────────────────────────────────────────────────────
#  INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_app_window(app_name: str):
    """Find the window for an app using registry patterns. Returns window or None."""
    try:
        import pygetwindow as gw
        from app.src.app_registry import registry
        patterns = registry.get_all_window_patterns(app_name)
        all_wins = gw.getAllWindows()
        # Try registry patterns first
        for pattern in patterns:
            for w in all_wins:
                try:
                    if re.match(pattern, w.title, re.IGNORECASE):
                        return w
                except re.error:
                    if pattern.lower() in w.title.lower():
                        return w
        # Fallback: substring match
        for w in all_wins:
            if app_name.lower() in w.title.lower() and w.title.strip():
                return w
    except Exception:
        pass
    return None


def _launch_and_wait(app_name: str, max_wait: float = 8.0) -> tuple[bool, str]:
    """
    Launch an app via AppRegistry and wait up to max_wait seconds for its
    window to appear. Returns (success: bool, message: str).
    """
    from app.src.app_registry import registry

    # Find real exe path
    exe = registry.get(app_name)

    # Launch
    launched = False
    if exe and os.path.exists(exe):
        try:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            launched = True
        except Exception:
            pass

    if not launched:
        # Shell fallback (works for Store apps like WhatsApp that are in PATH via AppxManifest)
        try:
            subprocess.Popen(app_name, shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            launched = True
        except Exception:
            pass

    if not launched:
        return False, f"Could not launch '{app_name}'. Run scan_installed_apps() or register_app_path()."

    # Poll for window
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(0.7)
        win = _get_app_window(app_name)
        if win:
            try:
                win.activate()
                time.sleep(0.4)
            except Exception:
                pass
            return True, f"'{app_name}' opened. Window: {win.title}"

    return False, (
        f"'{app_name}' launched but window not found after {max_wait}s.\n"
        f"The app may still be loading. Try again in a moment, "
        f"or open it manually."
    )


def _ensure_app_focused(app_name: str, max_wait: float = 8.0) -> tuple[bool, str]:
    """
    Ensure app window is visible and focused.
    Opens the app if not already running.
    """
    # Already open?
    win = _get_app_window(app_name)
    if win:
        try:
            win.activate()
            time.sleep(0.4)
        except Exception:
            pass
        return True, f"'{app_name}' already open. Focused: {win.title}"

    # Need to launch
    return _launch_and_wait(app_name, max_wait=max_wait)


def _search_and_open_contact(app_name: str, contact_name: str) -> bool:
    """
    Search for a contact in WhatsApp or Telegram using keyboard shortcuts.
    Returns True if search was executed (window was focused).
    """
    try:
        import pyautogui
        import pyperclip

        win = _get_app_window(app_name)
        if not win:
            return False

        win.activate()
        time.sleep(0.5)

        # Universal search: Ctrl+F works in both WhatsApp and Telegram
        pyautogui.hotkey("ctrl", "f")
        time.sleep(0.8)

        # Clear and type contact name via clipboard (handles Unicode names)
        pyperclip.copy(contact_name)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(1.2)

        # Select first result
        pyautogui.press("down")
        time.sleep(0.3)
        pyautogui.press("enter")
        time.sleep(0.8)
        return True
    except Exception:
        return False


def _type_and_send_message(message: str) -> bool:
    """Paste message into focused chat input and press Enter."""
    try:
        import pyautogui
        import pyperclip
        pyperclip.copy(message)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)
        pyautogui.press("enter")
        time.sleep(0.4)
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  TOOLS
# ─────────────────────────────────────────────────────────────────────────────

@tool
def send_whatsapp_message(contact_name: str, message: str) -> str:
    """Send a WhatsApp message to a contact using WhatsApp Desktop.

    Automatically finds and launches WhatsApp using the AppRegistry —
    works for Store installs, AppData installs, and PATH installs.
    Does NOT require the app to be open in advance.

    Args:
        contact_name: Contact name exactly as shown in WhatsApp
        message: Message text to send
    """
    try:
        import pyautogui
        import pyperclip
    except ImportError:
        return "ERROR: pip install pyautogui pyperclip pygetwindow"

    ok, status = _ensure_app_focused("whatsapp", max_wait=8.0)
    if not ok:
        return f"WhatsApp not available: {status}"

    searched = _search_and_open_contact("whatsapp", contact_name)
    if not searched:
        return f"Could not search for contact '{contact_name}' in WhatsApp."

    sent = _type_and_send_message(message)
    if sent:
        return f"WhatsApp message sent to '{contact_name}': {message[:80]}"
    return "Message typed but could not press Enter. Check if chat is open."


@tool
def send_telegram_message(contact_or_group: str, message: str) -> str:
    """Send a Telegram message using Telegram Desktop.

    Automatically finds and launches Telegram using the AppRegistry.
    Works for AppData and Store installs.

    Args:
        contact_or_group: Contact or group name
        message: Message text to send
    """
    try:
        import pyautogui
        import pyperclip
    except ImportError:
        return "ERROR: pip install pyautogui pyperclip pygetwindow"

    ok, status = _ensure_app_focused("telegram", max_wait=8.0)
    if not ok:
        return f"Telegram not available: {status}"

    searched = _search_and_open_contact("telegram", contact_or_group)
    if not searched:
        return f"Could not search for '{contact_or_group}' in Telegram."

    sent = _type_and_send_message(message)
    if sent:
        return f"Telegram message sent to '{contact_or_group}': {message[:80]}"
    return "Message typed but could not press Enter. Check if chat is open."


@tool
def send_email(to_address: str, subject: str, body: str, cc: str = "") -> str:
    """Send email via the system default mail client (mailto: protocol).

    Args:
        to_address: Recipient email address
        subject: Subject line
        body: Email body
        cc: Optional CC address
    """
    try:
        import pyautogui
        import urllib.parse
        params: dict = {"subject": subject, "body": body}
        if cc:
            params["cc"] = cc
        mailto = "mailto:" + to_address + "?" + urllib.parse.urlencode(params)
        os.startfile(mailto)
        time.sleep(2.5)
        pyautogui.hotkey("ctrl", "enter")
        return f"Email sent to {to_address}"
    except Exception as e:
        return f"Email failed: {e}"


@tool
def share_links_whatsapp(contact_name: str, links: list, intro_text: str = "") -> str:
    """Share a list of links via WhatsApp.

    Args:
        contact_name: WhatsApp contact name
        links: List of URLs
        intro_text: Optional intro message
    """
    text = "\n".join(str(l) for l in links)
    msg = f"{intro_text}\n\n{text}" if intro_text else text
    return send_whatsapp_message.invoke({"contact_name": contact_name, "message": msg})


@tool
def share_links_telegram(contact_or_group: str, links: list, intro_text: str = "") -> str:
    """Share a list of links via Telegram.

    Args:
        contact_or_group: Telegram contact or group
        links: List of URLs
        intro_text: Optional intro message
    """
    text = "\n".join(str(l) for l in links)
    msg = f"{intro_text}\n\n{text}" if intro_text else text
    return send_telegram_message.invoke({"contact_or_group": contact_or_group, "message": msg})


@tool
def open_whatsapp_web(contact_name: str = "") -> str:
    """Open WhatsApp Web in the browser as a fallback when Desktop is unavailable.

    Args:
        contact_name: Optional contact to mention in the return message
    """
    import webbrowser
    webbrowser.open("https://web.whatsapp.com")
    note = f" Search for '{contact_name}' in the search bar." if contact_name else ""
    return f"Opened WhatsApp Web in browser.{note}"


@tool
def compose_message_text(task_description: str, links: list = None, data: str = "") -> str:
    """Format a well-structured message before sending via WhatsApp/Telegram/Email.

    Args:
        task_description: What the message is about
        links: Optional list of URLs to append
        data: Optional extra content
    """
    parts = [f"From Sentinel AI:\n{task_description}"]
    if data:
        parts.append(data)
    if links:
        parts.append("\nLinks:")
        parts.extend(f"{i}. {l}" for i, l in enumerate(links or [], 1))
    return "\n\n".join(parts)


@tool
def check_app_open(app_name: str) -> str:
    """Check if an app is currently open and visible on screen.

    Args:
        app_name: e.g. 'whatsapp', 'telegram', 'discord'
    """
    win = _get_app_window(app_name)
    if win:
        return f"'{app_name}' is OPEN. Window title: '{win.title}'"
    return f"'{app_name}' is NOT currently open."


# ─────────────────────────────────────────────────────────────────────────────
#  AGENT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_comms_agent(llm) -> AgentExecutor:
    tools = [
        send_whatsapp_message,
        send_telegram_message,
        send_email,
        share_links_whatsapp,
        share_links_telegram,
        open_whatsapp_web,
        compose_message_text,
        check_app_open,
    ]

    system_prompt = """You are CommsAgent — you send messages via WhatsApp Desktop, Telegram Desktop, and Email.

The tools use the AppRegistry to automatically find and launch the correct app.
You do NOT need to open apps manually first — the tools handle everything.

TOOLS:
- send_whatsapp_message(contact, message)          → finds WhatsApp, opens it, sends message
- send_telegram_message(contact_or_group, message) → finds Telegram, opens it, sends message
- send_email(to, subject, body)                    → opens default mail client, sends
- share_links_whatsapp(contact, links, intro)      → send list of URLs via WhatsApp
- share_links_telegram(contact, links, intro)      → send list of URLs via Telegram
- open_whatsapp_web(contact)                       → fallback: use WhatsApp Web in browser
- compose_message_text(task, links, data)          → format message before sending
- check_app_open(app_name)                         → verify if app window is visible

RULES:
1. Call send_whatsapp_message or send_telegram_message DIRECTLY — they launch the app.
2. If the tool reports app not found, use check_app_open() then open_whatsapp_web() fallback.
3. Use exactly the contact name the user specified — never invent or modify it.
4. Confirm what was sent and to whom in your final reply."""

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent, tools=tools, verbose=True,
        handle_parsing_errors=True, max_iterations=6,
    )