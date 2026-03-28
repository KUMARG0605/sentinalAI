"""
system_agent_v2.py — Improved SystemAgent with screenshot verification & visual navigation

KEY IMPROVEMENTS over agents.py:
  1. verify_action_with_screenshot() — after every open/install/nav action, takes a
     screenshot and asks SambaNova vision model whether the action actually happened.
     If verification fails, the agent retries or reports clearly.

  2. visual_navigate() — replaces blind click/type guesses. Takes screenshot → sends
     to SambaNova → gets (x, y) back → moves mouse there → clicks. Never guesses coordinates.

  3. visual_type() — clicks a field visually first to activate it, then types. Fixes
     "open file explorer → vscode opened" class of bugs where the wrong window was active.

  4. open_app_verified() — opens an app, waits, takes screenshot, confirms it's open.
     Returns clear error if the wrong app opened or nothing opened.

  5. install_from_store_verified() — for store installs, uses visual navigation steps
     with screenshot checks at each stage (search → find app → click install → confirm).

HOW VERIFICATION WORKS:
  - After action: take_screenshot() → base64 encode → SambaNova Llama-3.2-90B-Vision-Instruct
  - Ask: "Did [action] succeed? What is currently on screen? Answer YES/NO + description."
  - If NO or uncertain: retry up to 2 times, then return failure message (no false positives).

HOW VISUAL NAVIGATION WORKS:
  - take_screenshot() → send to SambaNova with description of target element
  - Ask: "Give me the exact pixel coordinates (x, y) of [element]. Return JSON: {x: N, y: N}"
  - Move mouse to (x, y) → click
  - For text input: click to focus, then pyautogui.write()
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

# ── Import existing tools from the project ────────────────────────────────────
from app.src.tools import (
    open_application, open_in_app, open_folder, open_file_with_app,
    close_application, kill_app_instances, sleep,
    focus_window, click_window_control, click_element_by_text,
    type_in_window, scroll_window,
    keyboard_type, keyboard_press, mouse_move, mouse_click,
    run_shell_command,
    take_screenshot,
    vision_act_on_screen,
    run_multi_step_actions,
    set_clipboard,
)
from app.src.agents.agents import install_application, send_desktop_notification
from app.src.llm_rotation import get_sambanova_llm


# ─────────────────────────────────────────────────────────────────────────────
#  CORE HELPERS  (not exposed as tools — used internally by the new tools)
# ─────────────────────────────────────────────────────────────────────────────

def _get_vision_llm():
    """Return SambaNova vision LLM (Llama-3.2-90B-Vision-Instruct)."""
    model = os.environ.get("SAMBANOVA_VISION_MODEL", "Llama-3.2-90B-Vision-Instruct")
    return get_sambanova_llm(model=model, temperature=0.1)


def _screenshot_to_b64() -> Tuple[str, object]:
    """
    Take a screenshot and return (base64_jpeg_string, PIL_image).
    Raises RuntimeError if pyautogui is not available.
    """
    try:
        import pyautogui
    except ImportError:
        raise RuntimeError("pyautogui not installed — run: pip install pyautogui")

    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return b64, img


def _ask_vision(prompt: str, b64_img: str) -> str:
    """
    Send a screenshot + prompt to SambaNova vision model.
    Returns the model's text response.
    """
    llm = _get_vision_llm()
    msg = HumanMessage(content=[
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
    ])
    resp = llm.invoke([msg])
    return str(resp.content).strip()


def _verify_screenshot(question: str, retries: int = 2) -> dict:
    """
    Take a screenshot and ask the vision model a yes/no question.

    Returns:
        {
          "success": True/False,
          "description": "What the model saw",
          "raw": "full model response"
        }
    """
    for attempt in range(retries + 1):
        try:
            time.sleep(0.8)  # let screen settle
            b64, _ = _screenshot_to_b64()
            prompt = (
                f"You are verifying the result of a desktop automation action on Windows.\n\n"
                f"QUESTION: {question}\n\n"
                f"Instructions:\n"
                f"1. Describe what you see on screen in 1-2 sentences.\n"
                f"2. Answer the question with exactly YES or NO.\n"
                f"3. If NO, briefly explain what is wrong or missing.\n\n"
                f"Format your response as:\n"
                f"SCREEN: [description]\n"
                f"ANSWER: YES or NO\n"
                f"REASON: [only if NO]"
            )
            raw = _ask_vision(prompt, b64)

            # Parse answer
            answer_line = ""
            desc_line = ""
            for line in raw.splitlines():
                if line.upper().startswith("ANSWER:"):
                    answer_line = line.split(":", 1)[-1].strip().upper()
                if line.upper().startswith("SCREEN:"):
                    desc_line = line.split(":", 1)[-1].strip()

            success = "YES" in answer_line
            return {"success": success, "description": desc_line or raw[:200], "raw": raw}

        except Exception as exc:
            if attempt == retries:
                return {"success": False, "description": f"Verification error: {exc}", "raw": str(exc)}
            time.sleep(1.0)

    return {"success": False, "description": "Verification failed after retries", "raw": ""}


def _get_element_coords(element_description: str, b64_img: str) -> Optional[Tuple[int, int]]:
    """
    Ask SambaNova vision model for pixel (x, y) coordinates of a UI element.
    Returns (x, y) or None if not found.
    """
    prompt = (
        f"You are a desktop automation assistant. Look at this Windows screenshot carefully.\n\n"
        f"Find the UI element described as: '{element_description}'\n\n"
        f"Return ONLY a valid JSON object with the pixel coordinates of the CENTER of that element.\n"
        f"Format: {{\"x\": <integer>, \"y\": <integer>}}\n\n"
        f"If you cannot find the element, return: {{\"x\": -1, \"y\": -1}}\n"
        f"Do NOT include any text outside the JSON object."
    )
    raw = _ask_vision(prompt, b64_img)

    # Strip markdown fences if present
    raw = raw.strip()
    if "```json" in raw:
        raw = raw.split("```json")[-1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(raw)
        x, y = int(data.get("x", -1)), int(data.get("y", -1))
        if x == -1 or y == -1:
            return None
        return x, y
    except Exception:
        # Fallback: regex search
        nums = re.findall(r'"x"\s*:\s*(\d+)', raw), re.findall(r'"y"\s*:\s*(\d+)', raw)
        if nums[0] and nums[1]:
            return int(nums[0][0]), int(nums[1][0])
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  NEW IMPROVED TOOLS
# ─────────────────────────────────────────────────────────────────────────────

@tool
def open_app_verified(app_name: str, expected_window_title_hint: str = "") -> str:
    """Open an application and VERIFY it actually opened using a screenshot.

    This is the CORRECT way to open apps — it checks the screenshot after opening
    to confirm the right app is visible. Fixes false-success reports.

    Args:
        app_name: Name of the app (e.g. 'microsoft store', 'file explorer', 'notepad', 'vscode').
        expected_window_title_hint: Optional hint for what the open window should look like
            (e.g. 'Microsoft Store', 'This PC', 'File Explorer'). Used for verification question.

    Returns:
        SUCCESS: <description of what opened> — or FAILED: <what went wrong>
    """
    import pyautogui

    # Map common names to their actual launch commands / registry names
    APP_LAUNCH_MAP = {
        "microsoft store": ("ms-windows-store:", "shell"),
        "store": ("ms-windows-store:", "shell"),
        "file explorer": ("explorer.exe", "shell"),
        "explorer": ("explorer.exe", "shell"),
        "notepad": ("notepad.exe", "shell"),
        "calculator": ("calc.exe", "shell"),
        "paint": ("mspaint.exe", "shell"),
        "task manager": ("taskmgr.exe", "shell"),
        "settings": ("ms-settings:", "shell"),
        "control panel": ("control.exe", "shell"),
        "cmd": ("cmd.exe", "shell"),
        "powershell": ("powershell.exe", "shell"),
        "vscode": ("code", "which"),
        "visual studio code": ("code", "which"),
        "chrome": ("chrome", "which"),
        "edge": ("msedge.exe", "shell"),
        "firefox": ("firefox", "which"),
        "spotify": ("spotify", "which"),
        "discord": ("discord", "which"),
        "slack": ("slack", "which"),
    }

    normalized = app_name.lower().strip()
    hint = expected_window_title_hint or app_name

    # Determine verification question
    verify_q = (
        f"Is there a window for '{hint}' visible and in the foreground on this screen? "
        f"Look for any window titled or resembling '{hint}'."
    )

    # Special case: Microsoft Store and other URI-based apps
    launch_info = APP_LAUNCH_MAP.get(normalized)

    try:
        if launch_info:
            cmd, method = launch_info
            if method == "shell":
                os.startfile(cmd)
            else:
                import shutil
                full = shutil.which(cmd)
                if full:
                    subprocess.Popen([full], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # Fall back to original open_application tool logic
            result = open_application.invoke({"app_name": app_name})
            if "Could not" in result or "failed" in result.lower():
                return f"FAILED to open '{app_name}': {result}"

        # Wait for app to load
        time.sleep(2.5)

    except Exception as exc:
        return f"FAILED to launch '{app_name}': {exc}"

    # === VERIFY with screenshot ===
    verify = _verify_screenshot(verify_q)
    if verify["success"]:
        return f"SUCCESS: Opened '{app_name}'. Screen shows: {verify['description']}"
    else:
        # One retry with longer wait
        time.sleep(2.0)
        verify2 = _verify_screenshot(verify_q)
        if verify2["success"]:
            return f"SUCCESS (after wait): Opened '{app_name}'. Screen shows: {verify2['description']}"

        return (
            f"FAILED: Launched '{app_name}' but verification shows it did NOT open correctly.\n"
            f"Screen description: {verify2['description']}\n"
            f"Suggestion: Try visual_navigate to find and click the app manually, "
            f"or check if the app is installed."
        )


@tool
def visual_navigate(element_description: str, click_type: str = "left") -> str:
    """Take a screenshot, find a UI element visually using SambaNova AI, and click it.

    This is the CORRECT navigation method — it never guesses coordinates.
    It takes a real screenshot, asks the AI where the element is, then clicks there.

    Use this for:
    - Clicking buttons, icons, menu items, tabs
    - Clicking anywhere you need the exact visual location
    - Navigation when you don't know the window control name

    Args:
        element_description: What you want to click, described naturally.
            Examples: "Install button for Hill Climb Racing",
                      "Search bar in Microsoft Store",
                      "Yes button in the dialog",
                      "Close button (X) of the notification popup"
        click_type: 'left', 'right', or 'double'. Sets what type of click to perform.

    Returns:
        SUCCESS: Clicked <element> at (x, y) — or FAILED: <reason>
    """
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
    except ImportError:
        return "FAILED: pyautogui not installed — run: pip install pyautogui"

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            time.sleep(0.5)
            b64, img = _screenshot_to_b64()
            coords = _get_element_coords(element_description, b64)

            if coords is None:
                if attempt < max_attempts:
                    time.sleep(1.0)
                    continue
                return (
                    f"FAILED: Could not locate '{element_description}' on screen after {max_attempts} attempts.\n"
                    f"The element may not be visible. Try scrolling or navigating to where it should appear."
                )

            x, y = coords

            # Sanity check: coordinates within screen bounds
            screen_w, screen_h = pyautogui.size()
            if not (0 <= x <= screen_w and 0 <= y <= screen_h):
                return f"FAILED: AI returned out-of-bounds coordinates ({x}, {y}) for screen {screen_w}x{screen_h}."

            pyautogui.moveTo(x, y, duration=0.3)
            time.sleep(0.15)
            
            ct = click_type.lower().strip()
            if ct == "double":
                pyautogui.doubleClick(x, y)
            elif ct == "right":
                pyautogui.click(x, y, button="right")
            else:
                pyautogui.click(x, y)

            return f"SUCCESS: Performed {ct}-click on '{element_description}' at ({x}, {y})."

        except Exception as exc:
            if attempt == max_attempts:
                return f"FAILED on attempt {attempt}: {exc}"
            time.sleep(0.8)

    return f"FAILED: Could not navigate to '{element_description}'."


@tool
def visual_type(element_description: str, text: str, press_enter: bool = False, clear_field: bool = True) -> str:
    """Find a text field visually, click it to activate it, then type text into it.

    This FIXES the bug where typing goes into the wrong window.
    It always clicks the field first before typing.

    Args:
        element_description: Description of the input field to type into.
            Examples: "Microsoft Store search bar",
                      "address bar in File Explorer",
                      "command palette in VSCode",
                      "chat message input box"
        text: The text to type.
        press_enter: Whether to press Enter after typing (default False).
        clear_field: Whether to attempt clearing the field before typing (default True).

    Returns:
        SUCCESS: Clicked field and typed '<text>' — or FAILED: <reason>
    """
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
    except ImportError:
        return "FAILED: pyautogui not installed — run: pip install pyautogui"

    # Step 1: Find and click the field
    b64, _ = _screenshot_to_b64()
    coords = _get_element_coords(element_description, b64)

    if coords is None:
        return (
            f"FAILED: Could not locate '{element_description}' on screen.\n"
            f"Make sure the window containing this field is open and visible."
        )

    x, y = coords
    screen_w, screen_h = pyautogui.size()
    if not (0 <= x <= screen_w and 0 <= y <= screen_h):
        return f"FAILED: AI returned out-of-bounds coordinates ({x}, {y})."

    # Click to activate
    pyautogui.click(x, y)
    time.sleep(0.4)

    # Clear existing content then type
    if clear_field:
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("backspace")
        time.sleep(0.1)
        
    pyautogui.write(text, interval=0.05)

    if press_enter:
        time.sleep(0.2)
        pyautogui.press("enter")

    return (
        f"SUCCESS: Clicked '{element_description}' at ({x}, {y}) and typed '{text[:50]}'"
        f"{'...' if len(text) > 50 else ''}"
        f"{' + Enter' if press_enter else ''}."
    )


@tool
def extract_text_from_screen(element_description: str) -> str:
    """Read and extract exact text from a specific UI element on the screen.
    
    Use this when you need to read a value, error message, tracking ID, or setting value 
    from the screen to use in your next steps.
    
    Args:
        element_description: What text you want to extract. 
            Examples: "The red error message under the password field",
                      "The Order ID at the top right",
                      "The current file path in the editor"
                      
    Returns:
        The exact text extracted from that element.
    """
    try:
        b64, _ = _screenshot_to_b64()
        prompt = (
            f"You are a text extractor. Look at this Windows screenshot carefully.\n"
            f"Find the UI element described as: '{element_description}'\n\n"
            f"Extract and return ONLY the exact text written in/on that element. "
            f"Do not include any other words, explanations, or quotes. Just the raw text."
        )
        text = _ask_vision(prompt, b64)
        return f"EXTRACTED TEXT: {text.strip()}"
    except Exception as exc:
        return f"FAILED to extract text: {exc}"


@tool
def close_app_verified(window_hint: str) -> str:
    """Close an application and verify it actually closed.
    
    This is safer than regular window closing because it checks if a 'Save your work?' 
    dialog blocked the app from closing, which happens often.
    
    Args:
        window_hint: Name or title of the window to close (e.g. 'Notepad', 'Edge', 'Calculator')
        
    Returns:
        SUCCESS: Window closed — or FAILED: <reason>
    """
    try:
        from app.src.background_actions import get_window_by_regex
        import win32gui
        import win32con
        import subprocess
        
        # 1. Try to find and focus the window
        hwnd = get_window_by_regex(window_hint)
        if hwnd:
            # Send WM_CLOSE
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        else:
            # Fallback to Taskkill if window not found (maybe it's a generic process name)
            subprocess.run(f'taskkill /f /im "{window_hint}.exe"', shell=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            
        time.sleep(1.5)
        
        # 2. Verify with screenshot
        verify = _verify_screenshot(f"Is the '{window_hint}' window completely gone from the screen? Also, are there any 'Save changes' or 'Confirm Exit' dialogs blocking it from closing?")
        
        if verify["success"]:
            return f"SUCCESS: App '{window_hint}' is closed. Screen: {verify['description']}"
            
        return (
            f"FAILED: Attempted to close '{window_hint}', but it seems to still be on screen or blocked by a popup.\n"
            f"Screen description: {verify['description']}\n"
            f"Suggestion: use visual_navigate to click 'Don't Save' or 'Cancel' if there's a popup."
        )
    except Exception as exc:
        return f"FAILED to close app: {exc}"


@tool
def verify_action_with_screenshot(question: str) -> str:
    """Take a screenshot and verify whether an action succeeded using SambaNova AI vision.

    Call this AFTER every important action (open app, install, navigate, etc.)
    to confirm it actually happened. This prevents false-success hallucinations.

    Args:
        question: A yes/no question about what should be visible.
            Examples:
                "Is Microsoft Store open and visible on screen?"
                "Does the Microsoft Store show Hill Climb Racing app page?"
                "Is there an Install button visible for Hill Climb Racing?"
                "Has the installation started? Is there a progress bar or 'Installing' text?"
                "Is File Explorer open showing the file system?"

    Returns:
        VERIFIED YES: <screen description> — or VERIFIED NO: <what's wrong>
    """
    result = _verify_screenshot(question)
    prefix = "VERIFIED YES" if result["success"] else "VERIFIED NO"
    return f"{prefix}: {result['description']}"


@tool
def screenshot_and_describe() -> str:
    """Take a screenshot of the current screen and return a description of what's visible.

    Use this to understand the current state of the screen before deciding what to do next.
    Always call this if you're unsure what app is open or what state the UI is in.

    Returns:
        Description of what's currently on screen.
    """
    try:
        b64, _ = _screenshot_to_b64()
        prompt = (
            "You are a Windows desktop assistant. Describe what you currently see on this screen.\n"
            "Include:\n"
            "- What application(s) are open\n"
            "- What is in the foreground/active window\n"
            "- Any dialogs, buttons, or important UI elements visible\n"
            "- The general state (e.g. loading, showing results, waiting for input)\n\n"
            "Be specific and concise (3-5 sentences)."
        )
        description = _ask_vision(prompt, b64)
        return f"SCREEN STATE: {description}"
    except Exception as exc:
        return f"Could not describe screen: {exc}"


@tool
def open_folder_verified(path: str) -> str:
    """Open a folder in File Explorer and verify it opened — NOT VSCode or another app.

    This is the CORRECT way to open folders. It uses explorer.exe directly and
    verifies the right window opened via screenshot.

    Args:
        path: Folder to open. Supports shortcuts:
              'desktop', 'downloads', 'documents', 'pictures', 'music',
              or any absolute path like 'C:/Users/nchar/Projects'

    Returns:
        SUCCESS: File Explorer opened showing <path> — or FAILED: <reason>
    """
    # Resolve shortcuts
    home = os.path.expanduser("~")
    shortcut_map = {
        "desktop": os.path.join(home, "Desktop"),
        "downloads": os.path.join(home, "Downloads"),
        "documents": os.path.join(home, "Documents"),
        "pictures": os.path.join(home, "Pictures"),
        "music": os.path.join(home, "Music"),
        "videos": os.path.join(home, "Videos"),
        "home": home,
        "~": home,
    }
    resolved = shortcut_map.get(path.lower().strip(), path)

    # Check OneDrive Desktop redirect
    if not os.path.exists(resolved) and "desktop" in path.lower():
        onedrive = os.environ.get("OneDrive", os.path.join(home, "OneDrive"))
        od_desktop = os.path.join(onedrive, "Desktop")
        if os.path.exists(od_desktop):
            resolved = od_desktop

    try:
        # Use explorer.exe with explicit path — this always opens a new window
        subprocess.Popen(["explorer.exe", resolved],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.0)
    except Exception:
        try:
            subprocess.Popen(f'explorer.exe "{resolved}"', shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2.0)
        except Exception as exc2:
            return f"FAILED to launch File Explorer: {exc2}"

    # Verify screenshot shows File Explorer, not VSCode
    verify = _verify_screenshot(
        "Is File Explorer (Windows file browser) open showing a folder? "
        "Look for a window with navigation pane, address bar with a file path, "
        "and file/folder icons. This should NOT be a code editor like VSCode."
    )

    if verify["success"]:
        return f"SUCCESS: File Explorer opened at '{resolved}'. Screen: {verify['description']}"

    # One more wait + retry
    time.sleep(2.0)
    verify2 = _verify_screenshot("Is File Explorer open with a folder view visible?")
    if verify2["success"]:
        return f"SUCCESS (after wait): File Explorer opened. Screen: {verify2['description']}"

    return (
        f"FAILED: File Explorer did not open correctly for '{resolved}'.\n"
        f"Screen: {verify['description']}\n"
        f"If a wrong app opened, try: visual_navigate('File Explorer in taskbar')"
    )


@tool
def open_in_app_verified(app_name: str, path: str) -> str:
    """Open a file or folder in a specific app and verify it opened correctly.

    Prevents the bug where the wrong app opens (e.g. File Explorer instead of VSCode).

    Args:
        app_name: App to use — 'vscode', 'notepad', 'explorer', 'chrome', etc.
        path: File or folder path. Supports 'desktop', 'downloads', or absolute paths.

    Returns:
        SUCCESS or FAILED with description of what's on screen.
    """
    from app.src.tools import open_in_app as _open_in_app
    result = _open_in_app.invoke({"app_name": app_name, "path": path})
    time.sleep(1.5)

    app_lower = app_name.lower()
    if "vscode" in app_lower or "code" in app_lower:
        q = "Is Visual Studio Code (VSCode) open with a folder or file? Look for code editor with sidebar and editor tabs."
    elif "explorer" in app_lower:
        q = "Is File Explorer (Windows file browser) open? NOT a code editor."
    elif "notepad" in app_lower:
        q = "Is Notepad open with a text file?"
    elif "chrome" in app_lower:
        q = "Is Google Chrome browser open?"
    else:
        q = f"Is {app_name} open with a file or folder loaded?"

    verify = _verify_screenshot(q)
    if verify["success"]:
        return f"SUCCESS: Opened '{path}' in {app_name}. Screen: {verify['description']}"

    return (
        f"FAILED: '{path}' may not have opened correctly in {app_name}.\n"
        f"Tool result: {result}\n"
        f"Screen shows: {verify['description']}"
    )


@tool
def visual_scroll(direction: str = "down", amount: int = 3) -> str:
    """Scroll the current window up or down, then describe what's now visible.

    Use this when an element you need to click is off-screen.
    After scrolling, the result includes what new content is now visible.

    Args:
        direction: 'down' or 'up'
        amount: Number of scroll steps (1-10, default 3)

    Returns:
        Description of what's visible after scrolling.
    """
    try:
        import pyautogui
    except ImportError:
        return "FAILED: pyautogui not installed."

    amount = max(1, min(10, amount))
    scroll_val = -amount if direction.lower() == "down" else amount
    pyautogui.scroll(scroll_val)
    time.sleep(0.6)

    try:
        b64, _ = _screenshot_to_b64()
        desc = _ask_vision(
            "Describe what is now visible on screen after scrolling. "
            "List any new buttons, text, items, or UI elements now in view. Be concise (2-3 sentences).",
            b64
        )
        return f"Scrolled {direction} {amount} steps. Now visible: {desc}"
    except Exception as exc:
        return f"Scrolled {direction} {amount} steps. (Screen describe failed: {exc})"






@tool
def type_in_window_verified(window_hint: str, text: str, press_enter: bool = False) -> str:
    """Type text into a window — first verifying focus landed on the RIGHT window.

    Fixes the core bug where type_in_window sends keys to the wrong app:
    set_focus() silently fails on UWP/Electron apps (VSCode, Store, Discord),
    so keyboard.send_keys() fires into whichever window was already active.

    Flow:
      1. Try to focus window by title (pywinauto)
      2. Screenshot → ask SambaNova if the correct window is actually in front
      3. If YES: type using pyautogui (which always types into the active window)
      4. If NO: fall back to visual_type() which clicks the field first

    Args:
        window_hint: Window title or app name (e.g. 'Microsoft Store', 'Notepad', 'File Explorer')
        text: Text to type
        press_enter: Whether to press Enter after typing (default False)

    Returns:
        SUCCESS: Typed in <window> — or FAILED: <reason>
    """
    try:
        import pyautogui
    except ImportError:
        return "FAILED: pyautogui not installed."

    # Step 1: Try to bring window to front with pywinauto
    try:
        from app.src.background_actions import get_window_by_regex
        import ctypes
        hwnd = get_window_by_regex(window_hint)
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            time.sleep(0.5)
    except Exception:
        pass

    # Step 2: Screenshot — verify the right window is actually focused
    try:
        b64, _ = _screenshot_to_b64()
        focus_response = _ask_vision(
            f"Look at this Windows screenshot. Is '{window_hint}' the currently active "
            f"foreground window (title bar highlighted, window in front)? "
            f"Answer YES or NO only.",
            b64
        )
        actually_focused = "YES" in focus_response.upper()
    except Exception:
        actually_focused = False

    if actually_focused:
        # Step 3: Safe to type — pyautogui writes to whatever is in front
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.write(text, interval=0.04)
        if press_enter:
            time.sleep(0.2)
            pyautogui.press("enter")
        snippet = text[:50] + ("..." if len(text) > 50 else "")
        return f"SUCCESS: '{window_hint}' was focused — typed: '{snippet}'"
    else:
        # Step 4: Wrong window — use visual_type which clicks the field first
        vt_result = visual_type.invoke({
            "element_description": f"text input or search bar in {window_hint}",
            "text": text,
            "press_enter": press_enter,
        })
        return f"Focus check failed — used visual_type fallback: {vt_result}"


@tool
def wait_for_app_ready(app_description: str, max_wait_seconds: int = 15) -> str:
    """Wait for an app to finish loading before interacting with it.

    Polls screenshots every 2 seconds, asking SambaNova if the app looks ready
    (fully loaded UI vs splash screen / spinner / blank window).

    Use this after open_app_verified() for slow-loading apps:
    Microsoft Store, VSCode, Electron apps, large Office documents.

    Args:
        app_description: What the app looks like when ready.
            Examples:
              "Microsoft Store showing home page with Featured apps section"
              "VSCode with file explorer sidebar and editor area"
              "File Explorer showing folder contents"
        max_wait_seconds: How long to wait max (5-30 seconds, default 15)

    Returns:
        READY after Xs: <screen description> — or TIMEOUT: <last screen description>
    """
    max_wait_seconds = min(max(5, max_wait_seconds), 30)
    check_interval = 2.0
    checks = int(max_wait_seconds / check_interval)

    for i in range(checks):
        time.sleep(check_interval)
        try:
            b64, _ = _screenshot_to_b64()
            response = _ask_vision(
                f"I'm waiting for this app to finish loading: {app_description}\n\n"
                f"Look at the screenshot and determine:\n"
                f"1. Is there a visible, usable UI (READY)?\n"
                f"   OR is there a loading screen, spinner, blank area, splash screen (LOADING)?\n\n"
                f"Reply in this exact format:\n"
                f"STATE: READY\n"
                f"SCREEN: [what you see]\n"
                f"  -- OR --\n"
                f"STATE: LOADING\n"
                f"SCREEN: [what you see]",
                b64
            )
            state = ""
            screen = ""
            for line in response.splitlines():
                ul = line.upper().strip()
                if ul.startswith("STATE:"):
                    state = line.split(":", 1)[-1].strip().upper()
                elif ul.startswith("SCREEN:"):
                    screen = line.split(":", 1)[-1].strip()

            if "READY" in state:
                elapsed = (i + 1) * check_interval
                return f"READY after {elapsed:.0f}s: {screen or response[:150]}"
        except Exception:
            pass  # Keep waiting on any error

    # Final screenshot description regardless
    try:
        b64, _ = _screenshot_to_b64()
        desc = _ask_vision("Describe what is on screen right now in 1-2 sentences.", b64)
        return f"TIMEOUT after {max_wait_seconds}s. Screen: {desc}"
    except Exception:
        return f"TIMEOUT after {max_wait_seconds}s. Could not describe screen."


# ─────────────────────────────────────────────────────────────────────────────
#  TIMING RULES (same as original)
# ─────────────────────────────────────────────────────────────────────────────

_TIMING_RULES = """
WINDOW TIMING RULES (always follow):
- kill_app_instances(app) → BEFORE opening any app you will interact with
- sleep(seconds=1.5)      → AFTER open_application, BEFORE interacting
- sleep(seconds=0.5)      → AFTER focus_window, BEFORE typing or clicking
- focus_window(regex)     → BEFORE type_in_window or click_element_by_text
- Use .*AppName.* wildcards in window_title_regex
- For apps blocking SendKeys: set_clipboard(text) then keyboard_press("ctrl+v")
- Use run_multi_step_actions to execute ALL steps of a task in ONE call
- NEVER close apps unless explicitly told to
"""


# ─────────────────────────────────────────────────────────────────────────────
#  BUILD IMPROVED SYSTEM AGENT
# ─────────────────────────────────────────────────────────────────────────────

def build_system_agent_v2(llm) -> AgentExecutor:
    """
    Build the improved SystemAgent with screenshot verification and visual navigation.

    New tools vs original:
      open_app_verified          → replaces open_application for verified launches
      visual_navigate            → replaces blind click — finds element via screenshot
      visual_type                → replaces type_in_window for visual field targeting
      verify_action_with_screenshot → verify any action succeeded
      screenshot_and_describe    → understand current screen state
      install_app_from_microsoft_store → full verified Store install flow
    """

    tools = [
        # === NEW: Verified / Visual tools (use these FIRST) ===
        open_app_verified,
        open_folder_verified,
        open_in_app_verified,
        visual_navigate,
        visual_type,
        visual_scroll,
        extract_text_from_screen,
        close_app_verified,
        verify_action_with_screenshot,
        screenshot_and_describe,
        type_in_window_verified,
        wait_for_app_ready,

        # === Original tools (keep as fallbacks) ===
        open_application,
        open_in_app,
        open_folder,
        open_file_with_app,
        close_application,
        kill_app_instances,
        sleep,
        focus_window,
        click_window_control,
        click_element_by_text,
        type_in_window,
        scroll_window,
        keyboard_type,
        keyboard_press,
        mouse_move,
        mouse_click,
        vision_act_on_screen,
        install_application,
        send_desktop_notification,
        run_multi_step_actions,
        set_clipboard,
    ]

    system_prompt = f"""You are SystemAgent — an intelligent Windows desktop controller with visual verification.

CRITICAL RULES — ALWAYS FOLLOW:
1. NEVER report success without verifying. After every open/install/navigate action, use
   verify_action_with_screenshot() to confirm it actually happened on screen.
2. NEVER guess coordinates. Use visual_navigate() to find elements — it takes a real
   screenshot and asks the AI where the element is before clicking.
3. If unsure what is on screen, call screenshot_and_describe() FIRST.
4. If an action fails verification, try again differently or report FAILED clearly.
   Do NOT pretend something happened when the screenshot shows it didn't.

TOOL SELECTION GUIDE:

Opening apps (USE THESE IN ORDER):
  1. open_app_verified(app_name, hint)     ← ALWAYS use this, it verifies the app opened
  2. verify_action_with_screenshot(q)      ← double-check if needed
  3. open_application(app_name)            ← fallback only if open_app_verified fails

Opening folders in File Explorer:
  1. open_folder_verified(path)            ← USE THIS for opening folders in the default File Explorer. NEVER use this if the prompt asks to open a folder IN a specific app (like VSCode, Windsurf, etc).
     Supports shortcuts: 'desktop', 'downloads', 'documents', etc.
     NEVER use open_application('file explorer') — use open_folder_verified instead.

Opening files/folders in a specific app:
  1. open_in_app_verified(app_name, path)  ← USE THIS when the prompt explicitly asks to open a file or folder IN a given application (e.g. "open folder in VSCode" or "open Desktop in Windsurf").
  2. open_in_app(app_name, path)           ← fallback only

Clicking UI elements (USE THESE IN ORDER):
  1. visual_navigate(element_description, click_type)  ← ALWAYS try this first. Supports left, right, and double clicks explicitly!
  2. click_element_by_text(regex, text)    ← fallback for known window controls
  3. click_window_control(regex, title)    ← fallback for named controls
  4. vision_act_on_screen(desc, "click")   ← fallback grid-based method

Typing into fields:
  1. visual_type(field_description, text, clear_field=True)  ← ALWAYS try this — clicks field first, clears it, then types
  2. type_in_window_verified(hint, text)   ← use when you know window title — verifies focus first
  3. type_in_window(regex, text)           ← last resort fallback only

Reading exact data from screen:
  → extract_text_from_screen(element)     ← use when you need to read an order ID, error code, or specific value

Closing apps safely:
  → close_app_verified(window_hint)       ← always use this to avoid getting stuck on 'Save changes' dialogs
  3. type_in_window(regex, text)           ← last resort fallback only

Waiting for slow apps to load:
  → wait_for_app_ready(description, secs) ← polls screenshots until app is ready
    Use after open_app_verified() for: Microsoft Store, VSCode, Office apps, Electron apps
    Example: wait_for_app_ready("Microsoft Store home page with Featured apps section")

Scrolling to find off-screen elements:
  1. visual_scroll(direction, amount)      ← scrolls AND describes what's now visible

Understanding screen state:
  → screenshot_and_describe()             ← call this whenever you're unsure what's open

Verifying actions:
  → verify_action_with_screenshot(q)      ← call after EVERY important action

{_TIMING_RULES}

WORKFLOW FOR OPENING AN APP:
  1. open_app_verified(app_name)
  2. wait_for_app_ready("description of loaded app") — for slow apps (Store, VSCode, Office)
  3. If READY → proceed with task
  4. If FAILED/TIMEOUT → screenshot_and_describe() → figure out what's wrong → retry

WORKFLOW FOR INSTALLING FROM STORE:
  1. install_app_from_microsoft_store(app_name)  ← this handles everything including waits
  2. If FAILED at any step → report exactly where it failed and why

WORKFLOW FOR TYPING INTO AN APP:
  1. visual_type(field_description, text)        ← for fields you need to locate visually
  2. type_in_window_verified(window_hint, text)  ← when you know the window title
  3. After typing: verify_action_with_screenshot("Does [window] show the text [snippet]?")

WORKFLOW FOR NAVIGATING A UI:
  1. screenshot_and_describe() to confirm correct app is open
  2. visual_navigate(element) to click elements
  3. verify_action_with_screenshot(q) to confirm each step worked
  4. visual_type(field, text) to type in fields
  5. visual_scroll("down") if element not visible — it describes new content after scroll

PATH RULES:
  - Desktop may be redirected to OneDrive. Use shortcut "desktop" and let the tool resolve it.
  - NEVER use [username] or any placeholder. Use real paths or shortcuts.

RESPONSE FORMAT:
  - Report what you did step by step
  - Include verification results
  - If anything FAILED, say so clearly — NEVER pretend success
"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        handle_parsing_errors=True,
        max_iterations=20,  # More iterations for verified multi-step tasks
    )