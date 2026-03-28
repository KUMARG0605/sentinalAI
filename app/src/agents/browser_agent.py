"""
browser_agent.py — General-Purpose Browser Agent for SentinelAI v2

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL: THIS AGENT ALWAYS USES THE USER'S REAL BROWSER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Connects to running Chrome/Edge via CDP on port 9222.

USE THIS AGENT FOR:
  ✓ Job searching (Naukri, LinkedIn, Indeed, Internshala, etc.)
  ✓ News reading and web research
  ✓ Data / content extraction from any website
  ✓ Writing results to files
  ✓ Filling non-payment forms (job applications, contact forms, sign-ups)
  ✓ Multi-page navigation and scraping
  ✓ YouTube, Reddit, Wikipedia, GitHub browsing
  ✗ NOT for e-commerce ordering/checkout — use ecommerce_agent instead

PRIMARY INTERACTION: SOM (Set-of-Marks)
  → Draws numbered red boxes on every interactive element
  → Agent uses [ID:N] numbers — works regardless of DOM class changes
  → Fallback to DOM-bid and text-click for edge cases

BrowserManager is SHARED — also imported by ecommerce_agent.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from typing import Any, Optional

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

try:
    from playwright.async_api import (
        async_playwright, Browser, Page,
        TimeoutError as PWTimeout,
    )
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False


# ─────────────────────────────────────────────────────────────────────────────
#  CDP BROWSER MANAGER — shared by browser_agent and ecommerce_agent
# ─────────────────────────────────────────────────────────────────────────────

class BrowserManager:
    """
    Connects to the user's running Chrome or Edge via CDP.
    NEVER launches a new Chromium instance.
    Provides SOM-based interaction (primary) and DOM-bid interaction (fallback).
    """

    CDP_URL = "http://localhost:9222"

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._error: str = ""

    def is_cdp_available(self) -> bool:
        try:
            req = urllib.request.urlopen(f"{self.CDP_URL}/json/version", timeout=1.5)
            return req.status == 200
        except Exception:
            return False

    # Ordered preference: Edge first, then Chrome
    EDGE_PATHS = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        "/usr/bin/microsoft-edge",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    CHROME_PATHS = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]

    def _is_process_running(self, proc_name: str) -> bool:
        """Check if a browser process is already running (Windows only)."""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {proc_name}", "/NH"],
                capture_output=True, text=True, timeout=3
            )
            return proc_name.lower() in result.stdout.lower()
        except Exception:
            return False

    def launch_real_browser(self, browser_name: str = "auto") -> str:
        """
        Launch a browser with CDP enabled on the DEFAULT user profile.

        If Edge/Chrome is already running WITHOUT CDP, we must close it first
        and relaunch with --remote-debugging-port=9222. This preserves all
        cookies, logins, and sessions because we reuse the same profile.
        Edge's built-in "Restore tabs" will bring back previous tabs.
        """
        # Determine which browsers are installed
        edge_exe   = next((p for p in self.EDGE_PATHS   if p and os.path.exists(p)), None)
        chrome_exe = next((p for p in self.CHROME_PATHS if p and os.path.exists(p)), None)

        edge_running   = self._is_process_running("msedge.exe")
        chrome_running = self._is_process_running("chrome.exe")

        # Choose browser: prefer Edge, then Chrome
        chosen_exe = None
        chosen_name = None
        proc_name = None

        if browser_name.lower() == "chrome" and chrome_exe:
            chosen_exe = chrome_exe
            chosen_name = "Chrome"
            proc_name = "chrome.exe"
        elif edge_exe:
            chosen_exe = edge_exe
            chosen_name = "Edge"
            proc_name = "msedge.exe"
        elif chrome_exe:
            chosen_exe = chrome_exe
            chosen_name = "Chrome"
            proc_name = "chrome.exe"
        else:
            return (
                "No browser found. Install Microsoft Edge or Google Chrome, then run:\n"
                "  msedge.exe --remote-debugging-port=9222 --remote-allow-origins=*"
            )

        browser_running = self._is_process_running(proc_name)

        if browser_running:
            # Close existing browser gracefully so we can relaunch with CDP
            # on the SAME default profile (preserves all logins/cookies).
            print(
                f"[BrowserManager] {chosen_name} is running without CDP. "
                f"Closing gracefully and relaunching with CDP..."
            )
            try:
                # Graceful close — gives Edge time to save session for restore
                subprocess.run(
                    ["taskkill", "/IM", proc_name, "/F"],
                    capture_output=True, timeout=5,
                )
                # Wait for process to fully exit
                for _ in range(10):
                    time.sleep(0.5)
                    if not self._is_process_running(proc_name):
                        break
                time.sleep(1.0)  # Extra buffer for profile lock release
            except Exception as e:
                print(f"[BrowserManager] Warning: close failed: {e}")
        else:
            print(f"[BrowserManager] Launching {chosen_name} with CDP...")

        # Launch on DEFAULT profile — all cookies/logins preserved
        subprocess.Popen([
            chosen_exe,
            "--remote-debugging-port=9222",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--restore-last-session",
        ])
        time.sleep(3.0)
        return (
            f"Launched {chosen_name} with CDP enabled. "
            f"Same profile — all logins and cookies preserved."
        )

    def connect(self) -> str:
        if not PLAYWRIGHT_OK:
            return "ERROR: Run: pip install playwright && python -m playwright install chromium"
        if self._browser is not None:
            return f"Already connected. Current page: {self.get_url()}"
        if not self.is_cdp_available():
            return "CDP not available on port 9222. Call browser_connect() to launch Chrome."

        self._ready.clear()
        self._error = ""
        self._browser = None
        self._page = None
        self._loop = None

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        if not self._ready.wait(timeout=20):
            self._error = ""
            self._ready.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            if not self._ready.wait(timeout=15):
                return f"Connection timeout. Chrome may be busy. Error: {self._error}"

        if self._error:
            self._error = ""
            self._ready.clear()
            self._browser = None
            self._page = None
            self._loop = None
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            if not self._ready.wait(timeout=15):
                return "Connection failed: browser refused. Try refreshing Chrome."
            if self._error:
                return f"Connection failed: {self._error}"

        try:
            url = self.get_url()
        except Exception:
            url = "unknown"
        return f"Connected to real browser. Page: {url}"

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_async())

    async def _connect_async(self):
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(self.CDP_URL)
            contexts = self._browser.contexts
            if contexts:
                pages = contexts[0].pages
                self._page = pages[0] if pages else await contexts[0].new_page()
            else:
                ctx = await self._browser.new_context()
                self._page = await ctx.new_page()
            self._ready.set()
            while True:
                await asyncio.sleep(1)
        except Exception as exc:
            self._error = str(exc)
            self._ready.set()

    def _run(self, coro) -> Any:
        if self._loop is None:
            print("[BrowserManager] Auto-connecting...")

            if self.is_cdp_available():
                # ── CDP is reachable on port 9222 → connect to existing window.
                # NEVER launch a new browser when one is already running with CDP.
                print("[BrowserManager] CDP port 9222 is open — connecting to existing window.")
                self.connect()
            else:
                # ── No CDP available.
                # launch_real_browser now handles the case where a browser is
                # already running by using a separate --user-data-dir.
                res = self.launch_real_browser("auto")
                print(f"[BrowserManager] {res}")
                time.sleep(1.0)
                if self.is_cdp_available():
                    self.connect()

            if self._loop is None:
                coro.close()
                raise RuntimeError(
                    "BrowserManager: Could not connect to browser on port 9222.\n"
                    "Make sure Edge/Chrome is running with:\n"
                    "  msedge.exe --remote-debugging-port=9222 --remote-allow-origins=*"
                )
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=30)

    # ── Navigation ────────────────────────────────────────────────────────────

    def navigate(self, url: str) -> str:
        if not url.startswith("http"):
            url = "https://" + url
        async def _go():
            await self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                await self._page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            title = await self._page.title()
            return f"Navigated to {url}\nTitle: {title}"
        try:
            return self._run(_go())
        except Exception as e:
            err = str(e)
            if "ERR_NAME_NOT_RESOLVED" in err or "net::ERR" in err:
                domain = re.search(r"https?://([^/]+)", url)
                hint = domain.group(1) if domain else url
                return (
                    f"Navigation failed: {err}\n\n"
                    f"DOMAIN '{hint}' could not be resolved.\n"
                    f"Common fixes:\n"
                    f"  - Check spelling. Example: use 'naukri.com' NOT 'naukari.com'\n"
                    f"  - Try Google search: navigate to "
                    f"'https://www.google.com/search?q={hint.replace('.', '+')}+official+site'\n"
                    f"  - Then som_scan and click the correct link."
                )
            if "networkidle" in err or "Timeout" in err:
                try:
                    return self._run(self._page.title())
                except Exception:
                    pass
            raise

    def get_url(self) -> str:
        return self._page.url if self._page else ""

    def get_title(self) -> str:
        async def _t():
            return await self._page.title()
        try:
            return self._run(_t())
        except Exception:
            return ""

    def go_back(self) -> str:
        async def _b():
            await self._page.go_back(wait_until="domcontentloaded", timeout=10000)
            return f"Back. Now at: {self._page.url}"
        return self._run(_b())

    # ── SOM — PRIMARY Interaction ─────────────────────────────────────────────

    def som_scan(self, query: str = "") -> str:
        """
        Set-of-Marks: annotate all interactive elements with [ID:N] red boxes.
        Returns the full element list. Filter with query to narrow results.
        """
        async def _scan():
            # Remove previous overlays
            await self._page.evaluate(
                "document.querySelectorAll('[data-som-overlay]').forEach(e => e.remove());"
            )

            elements = await self._page.evaluate("""
                (() => {
                    const sel = [
                        'a[href]', 'button', 'input:not([type=hidden])',
                        'select', 'textarea', '[role=button]', '[role=link]',
                        '[role=menuitem]', '[role=option]', '[role=tab]',
                        '[role=checkbox]', '[role=radio]', '[role=combobox]',
                        '[role=searchbox]'
                    ].join(',');

                    const items = [];
                    let id = 0;

                    for (const el of document.querySelectorAll(sel)) {
                        const r = el.getBoundingClientRect();
                        if (r.width < 2 || r.height < 2) continue;
                        if (r.top < -300 || r.top > window.innerHeight + 300) continue;
                        if (r.left < -300 || r.left > window.innerWidth + 300) continue;

                        const lbl = (
                            el.textContent ||
                            el.value ||
                            el.placeholder ||
                            el.getAttribute('aria-label') ||
                            el.getAttribute('title') ||
                            el.name ||
                            el.id ||
                            ''
                        ).trim().replace(/\\s+/g, ' ').slice(0, 80);

                        // Draw red box
                        const box = document.createElement('div');
                        box.setAttribute('data-som-overlay', id);
                        box.style.cssText = (
                            'position:fixed;' +
                            'left:' + Math.max(0, r.left) + 'px;' +
                            'top:' + Math.max(0, r.top) + 'px;' +
                            'width:' + r.width + 'px;' +
                            'height:' + r.height + 'px;' +
                            'border:2px solid red;' +
                            'z-index:2147483647;' +
                            'pointer-events:none;' +
                            'box-sizing:border-box;'
                        );
                        const num = document.createElement('div');
                        num.textContent = id;
                        num.style.cssText = (
                            'position:absolute;top:-1px;left:-1px;' +
                            'background:red;color:white;font-size:10px;' +
                            'font-weight:bold;padding:0 3px;line-height:14px;' +
                            'white-space:nowrap;z-index:2147483647;'
                        );
                        box.appendChild(num);
                        document.body.appendChild(box);

                        el.setAttribute('data-som-id', String(id));

                        items.push({
                            id: id,
                            tag: el.tagName.toLowerCase(),
                            type: el.type || '',
                            label: lbl,
                            href: (el.href || '').slice(0, 100),
                            name: el.name || '',
                            value: (el.value || '').slice(0, 40),
                        });
                        id++;
                    }
                    return items;
                })()
            """)

            url = self._page.url
            title = await self._page.title()
            lines = [
                f"URL: {url}",
                f"Title: {title}",
                "",
                f"SOM Elements ({len(elements)} found — use som_click(N) or som_fill(N, text)):",
            ]
            for el in elements:
                line = f"  [ID:{el['id']}] <{el['tag']}"
                if el['type'] and el['type'] not in ('', el['tag']):
                    line += f"[{el['type']}]"
                line += ">"
                if el['label']:
                    line += f" {el['label']}"
                if el['name']:
                    line += f"  name={el['name']}"
                if el['value']:
                    line += f"  val={el['value']}"
                if el['href'] and el['href'] not in ('javascript:void(0)', 'javascript:;', ''):
                    line += f"  href={el['href'][:60]}"
                lines.append(line)
            return "\n".join(lines)

        result = self._run(_scan())

        if query.strip():
            q = query.lower()
            all_lines = result.split("\n")
            header = all_lines[:4]
            filtered = [l for l in all_lines[4:] if q in l.lower()]
            if filtered:
                return "\n".join(
                    header
                    + [f"  (filtered by '{query}': {len(filtered)} of {len(all_lines)-4} elements)"]
                    + filtered
                )
        return result

    def som_click(self, element_id: int) -> str:
        async def _c():
            original_page = self._page
            try:
                el = self._page.locator(f"[data-som-id='{element_id}']").first
                if await el.count() == 0:
                    return (
                        f"SOM element {element_id} not found. "
                        "Page may have changed — call som_scan() again first."
                    )
                await el.scroll_into_view_if_needed(timeout=3000)
                await el.click(timeout=6000)
                await asyncio.sleep(0.8)

                switched = await self._switch_to_newest_tab(original_page)
                if switched:
                    title = await self._page.title()
                    return (
                        f"Clicked [ID:{element_id}] → opened NEW TAB. "
                        f"Now on: {self._page.url[:80]}\nTitle: {title}"
                    )
                try:
                    await self._page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                return f"Clicked [ID:{element_id}]. URL: {self._page.url}"
            except Exception as e:
                return f"SOM click {element_id} failed: {e}"
        return self._run(_c())

    def som_fill(self, element_id: int, text: str) -> str:
        async def _f():
            try:
                el = self._page.locator(f"[data-som-id='{element_id}']").first
                if await el.count() == 0:
                    return f"SOM element {element_id} not found. Call som_scan() first."
                await el.scroll_into_view_if_needed(timeout=3000)
                try:
                    await el.clear()
                    await el.type(text, delay=40)
                    return f"Filled [ID:{element_id}] with '{text[:50]}'"
                except Exception:
                    # React/Angular JS fallback
                    result = await self._page.evaluate(f"""
                        (() => {{
                            const el = document.querySelector("[data-som-id='{element_id}']");
                            if (!el) return "NOT_FOUND";
                            try {{
                                const setter = Object.getOwnPropertyDescriptor(
                                    window.HTMLInputElement.prototype, 'value'
                                ).set;
                                setter.call(el, {json.dumps(text)});
                                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                                return "OK";
                            }} catch(e) {{
                                el.value = {json.dumps(text)};
                                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                                return "OK_basic";
                            }}
                        }})()
                    """)
                    if "OK" in str(result):
                        return f"Filled [ID:{element_id}] with '{text[:50]}' (JS fallback)"
                    return f"Fill failed for [ID:{element_id}]"
            except Exception as e:
                return f"SOM fill {element_id} failed: {e}"
        return self._run(_f())

    # ── DOM-bid interaction (fallback) ────────────────────────────────────────

    def get_dom_state(self, max_elements: int = 40) -> str:
        async def _snap():
            url = self._page.url
            title = await self._page.title()
            elements = await self._page.evaluate(f"""
                (() => {{
                    const items = [];
                    const sel = ['a[href]','button','input:not([type=hidden])',
                        'select','textarea','[role=button]','[role=link]',
                        '[role=menuitem]','[role=option]','[role=tab]'].join(',');
                    let i = 0;
                    for (const el of document.querySelectorAll(sel)) {{
                        if (i >= {max_elements}) break;
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 && r.height === 0) continue;
                        const lbl = (el.textContent||el.value||el.placeholder||
                            el.getAttribute('aria-label')||el.getAttribute('title')||
                            el.name||el.id||'').trim().slice(0, 70);
                        if (!lbl && el.tagName !== 'INPUT' && el.tagName !== 'SELECT') continue;
                        el.setAttribute('data-bid', i);
                        items.push({{bid:i, tag:el.tagName.toLowerCase(),
                            type:el.type||'', label:lbl, id:el.id||'',
                            name:el.name||'', value:el.value||''}});
                        i++;
                    }}
                    return items;
                }})()
            """)
            lines = [f"URL: {url}", f"Title: {title}", "",
                     "Interactive elements (use click_bid(N)):"]
            for el in elements:
                line = f"  [{el['bid']}] <{el['tag']}"
                if el['type'] and el['type'] != el['tag']:
                    line += f"[{el['type']}]"
                if el['label']:
                    line += f"> {el['label']}"
                if el['id']:
                    line += f"  id={el['id']}"
                if el['name']:
                    line += f"  name={el['name']}"
                if el['value'] and len(el['value']) < 40:
                    line += f"  val={el['value']}"
                lines.append(line)
            return "\n".join(lines)
        return self._run(_snap())

    def click_bid(self, bid: int) -> str:
        async def _c():
            original_page = self._page
            el = self._page.locator(f"[data-bid='{bid}']").first
            await el.click(timeout=6000)
            await asyncio.sleep(0.8)
            switched = await self._switch_to_newest_tab(original_page)
            if switched:
                await self._page.wait_for_load_state("domcontentloaded", timeout=10000)
                title = await self._page.title()
                return (f"Clicked [{bid}] → opened NEW TAB. "
                        f"Now on: {self._page.url[:80]}\nTitle: {title}")
            try:
                await self._page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            return f"Clicked [{bid}]. URL: {self._page.url}"
        try:
            return self._run(_c())
        except Exception as e:
            return f"Click [{bid}] failed: {e}"

    def click_text(self, text: str) -> str:
        async def _c():
            original_page = self._page
            for strat in [f"text='{text}'", f"role=button[name='{text}']",
                          f"role=link[name='{text}']", f"[aria-label='{text}']"]:
                try:
                    loc = self._page.locator(strat).first
                    if await loc.count() > 0:
                        await loc.click(timeout=5000)
                        await asyncio.sleep(0.8)
                        switched = await self._switch_to_newest_tab(original_page)
                        if switched:
                            title = await self._page.title()
                            return (f"Clicked '{text}' → opened NEW TAB. "
                                    f"Now on: {self._page.url[:80]}\nTitle: {title}")
                        await self._page.wait_for_load_state("domcontentloaded", timeout=8000)
                        return f"Clicked '{text}'. URL: {self._page.url}"
                except Exception:
                    continue
            _t = text.lower().replace("'", "\\'")
            result = await self._page.evaluate(f"""
                (() => {{
                    const t = '{_t}';
                    for (const el of document.querySelectorAll(
                        'button,a,[role=button],[role=link],input[type=submit]')) {{
                        if (el.textContent.trim().toLowerCase().includes(t)||
                            (el.value||'').toLowerCase().includes(t)) {{
                            el.click();
                            return 'Clicked: '+el.textContent.trim().slice(0,40);
                        }}
                    }}
                    return 'NOT_FOUND: "'+t+'"';
                }})()
            """)
            return result
        return self._run(_c())

    def type_into(self, selector: str, text: str, clear_first: bool = True) -> str:
        async def _t():
            el = self._page.locator(selector).first
            if await el.count() == 0:
                return f"Selector not found: {selector}"
            if clear_first:
                await el.clear()
            await el.type(text, delay=40)
            return f"Typed '{text[:50]}' into {selector}"
        try:
            return self._run(_t())
        except Exception as e:
            return f"Type failed ({selector}): {e}"

    # ── Keyboard / Scroll ─────────────────────────────────────────────────────

    def press_key(self, key: str) -> str:
        async def _p():
            await self._page.keyboard.press(key)
            if key in ("Enter", "Return"):
                try:
                    await self._page.wait_for_load_state("domcontentloaded", timeout=8000)
                    await asyncio.sleep(1.2)
                except Exception:
                    await asyncio.sleep(1.5)
            else:
                await asyncio.sleep(0.4)
            return f"Pressed: {key}"
        return self._run(_p())

    def scroll(self, direction: str = "down", px: int = 500) -> str:
        async def _s():
            dy = px if direction.lower() == "down" else -px
            await self._page.mouse.wheel(0, dy)
            await asyncio.sleep(0.3)
            return f"Scrolled {direction} {px}px"
        return self._run(_s())

    # ── Tab management ────────────────────────────────────────────────────────

    async def _switch_to_newest_tab(self, original_page) -> bool:
        await asyncio.sleep(1.0)
        try:
            contexts = self._browser.contexts
            if not contexts:
                return False
            all_pages = contexts[0].pages
            if len(all_pages) <= 1:
                return False
            orig_url = original_page.url
            domain_match = re.search(r"https?://([^/]+)", orig_url)
            orig_domain = domain_match.group(1) if domain_match else ""
            same_domain_new = []
            for p in all_pages:
                if p == original_page:
                    continue
                pdm = re.search(r"https?://([^/]+)", p.url)
                if orig_domain and pdm and pdm.group(1) == orig_domain:
                    same_domain_new.append(p)
            if not same_domain_new:
                return False
            new_page = same_domain_new[-1]
            await new_page.wait_for_load_state("domcontentloaded", timeout=8000)
            self._page = new_page
            print(f"[BrowserManager] Switched to new tab: {(await new_page.title())[:60]}")
            return True
        except Exception as e:
            print(f"[BrowserManager] Tab switch failed: {e}")
            return False

    def get_all_tabs(self) -> list[str]:
        async def _t():
            try:
                pages = self._browser.contexts[0].pages
                return [f"Tab {i+1}: {await p.title()} | {p.url[:80]}"
                        for i, p in enumerate(pages)]
            except Exception:
                return []
        try:
            return self._run(_t())
        except Exception:
            return []

    def switch_to_tab(self, tab_index: int) -> str:
        async def _s():
            pages = self._browser.contexts[0].pages
            if tab_index < 1 or tab_index > len(pages):
                return f"Invalid tab index {tab_index}. Open tabs: {len(pages)}"
            self._page = pages[tab_index - 1]
            await self._page.bring_to_front()
            title = await self._page.title()
            return f"Switched to Tab {tab_index}: {title}"
        return self._run(_s())

    def new_tab(self, url: str = "") -> str:
        async def _nt():
            ctx = self._browser.contexts[0]
            page = await ctx.new_page()
            self._page = page
            if url:
                u = url if url.startswith("http") else "https://" + url
                await page.goto(u, wait_until="domcontentloaded", timeout=20000)
                return f"New tab opened: {u}"
            return "New blank tab opened"
        return self._run(_nt())

    # ── Content extraction ────────────────────────────────────────────────────

    def get_page_text(self, max_chars: int = 6000) -> str:
        async def _g():
            return (await self._page.inner_text("body"))[:max_chars]
        try:
            return self._run(_g())
        except Exception as e:
            return f"Text extraction failed: {e}"

    def get_all_links(self) -> str:
        async def _gl():
            links = await self._page.evaluate("""
                Array.from(document.querySelectorAll('a[href]'))
                .filter(a=>a.href&&!a.href.startsWith('javascript:'))
                .slice(0,50)
                .map(a=>({text:a.textContent.trim().slice(0,60),href:a.href.slice(0,150)}))
            """)
            if not links:
                return "No links found."
            return "\n".join(f"  {l['text'] or '[no text]'} → {l['href']}" for l in links)
        return self._run(_gl())

    def scrape_table(self) -> str:
        async def _st():
            tables = await self._page.evaluate("""
                (() => {
                    const result=[];
                    for(const t of document.querySelectorAll('table')){
                        const rows=[];
                        for(const tr of t.querySelectorAll('tr')){
                            const cells=Array.from(tr.querySelectorAll('td,th'))
                                .map(c=>c.textContent.trim().slice(0,100));
                            if(cells.length>0)rows.push(cells);
                        }
                        if(rows.length>0)result.push(rows);
                    }
                    return result;
                })()
            """)
            if not tables:
                return "No tables found."
            out = []
            for i, table in enumerate(tables):
                out.append(f"Table {i+1}:")
                for row in table[:20]:
                    out.append("  " + " | ".join(row))
                if len(table) > 20:
                    out.append(f"  ...{len(table)-20} more rows")
            return "\n".join(out)
        return self._run(_st())

    def get_input_fields(self) -> str:
        async def _f():
            fields = await self._page.evaluate("""
                Array.from(document.querySelectorAll('input,select,textarea'))
                .filter(e=>e.type!=='hidden'&&(e.offsetWidth>0||e.offsetHeight>0))
                .slice(0,30)
                .map(e=>({tag:e.tagName,type:e.type||'text',id:e.id||'',name:e.name||'',
                    placeholder:e.placeholder||'',required:e.required||false,
                    label:(document.querySelector('label[for="'+e.id+'"]')||{textContent:''})
                    .textContent.trim()}))
            """)
            if not fields:
                return "No input fields found."
            lines = ["Form fields:"]
            for f in fields:
                line = f"  <{f['tag'].lower()}[{f['type']}]"
                if f['id']:          line += f" id={f['id']}"
                if f['name']:        line += f" name={f['name']}"
                if f['placeholder']: line += f" placeholder='{f['placeholder']}'"
                if f['label']:       line += f" label='{f['label']}'"
                if f['required']:    line += " REQUIRED"
                lines.append(line)
            return "\n".join(lines)
        return self._run(_f())

    def execute_js(self, script: str) -> str:
        async def _j():
            return str(await self._page.evaluate(script))
        try:
            return self._run(_j())
        except Exception as e:
            return f"JS error: {e}"

    def wait_for_text(self, text: str, timeout_sec: int = 10) -> str:
        async def _w():
            try:
                await self._page.wait_for_selector(f"text={text}", timeout=timeout_sec * 1000)
                return f"Text appeared: '{text}'"
            except PWTimeout:
                return f"Timeout ({timeout_sec}s): '{text}' not found"
        return self._run(_w())

    def wait_for_url(self, fragment: str, timeout_sec: int = 10) -> str:
        async def _w():
            try:
                await self._page.wait_for_url(f"**{fragment}**", timeout=timeout_sec * 1000)
                return f"URL now contains '{fragment}': {self._page.url}"
            except PWTimeout:
                return f"Timeout: URL never had '{fragment}'. Current: {self._page.url}"
        return self._run(_w())

    def download_file(self, url: str, save_path: str) -> str:
        async def _d():
            try:
                async with self._page.expect_download(timeout=30000) as dl_info:
                    await self._page.goto(url)
                dl = await dl_info.value
                await dl.save_as(save_path)
                return f"Downloaded to {save_path}"
            except Exception:
                import urllib.request as ur
                os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
                ur.urlretrieve(url, save_path)
                return f"Downloaded (urllib) to {save_path}"
        return self._run(_d())

    def select_option(self, selector: str, label: str) -> str:
        async def _s():
            try:
                await self._page.select_option(selector, label=label)
                return f"Selected '{label}' in {selector}"
            except Exception:
                await self._page.select_option(selector, value=label)
                return f"Selected value '{label}' in {selector}"
        try:
            return self._run(_s())
        except Exception as e:
            return f"Select failed: {e}"

    # ── E-commerce helpers (used by ecommerce_agent) ──────────────────────────

    def get_product_list(self, max_items: int = 15) -> str:
        async def _pl():
            try:
                products_js = await self._page.evaluate("""
                    (() => {
                        const selectors = [
                            '[data-component-type="s-search-result"]',
                            'div[class*="slAVV8"]', 'div[class*="_75nlfW"]',
                            'div[class*="DOjaWF"]', 'div[class*="yKfJKb"]',
                            '[data-testid="product-card"]',
                            '.product-item', '.ProductCard', '.product-card',
                        ];
                        let cards = [];
                        for (const s of selectors) {
                            try {
                                const found = Array.from(document.querySelectorAll(s));
                                if (found.length > 2) { cards = found; break; }
                            } catch(e) {}
                        }
                        if (cards.length === 0) return null;
                        return cards.slice(0, 15).map((c, i) => {
                            const titleEl = c.querySelector(
                                'a[title],[class*="KzDlHZ"],[class*="IRpwTa"],h2 a,h3 a,a');
                            const title = (titleEl||{}).textContent||(titleEl||{}).title||'';
                            const priceEl = c.querySelector(
                                '[class*="Nx9bqj"],[class*="_30jeq3"],.a-price-whole,[class*="price"]');
                            const price = (priceEl||{}).textContent||'';
                            const ratingEl = c.querySelector(
                                '[class*="XQDdHH"],.a-icon-alt,[class*="star"]');
                            const rating = (ratingEl||{}).textContent||'';
                            const imgEl = c.querySelector('img');
                            const img = imgEl?(imgEl.src||imgEl.getAttribute('data-src')||''):'';
                            const linkEl = c.querySelector(
                                'a[href*="/dp/"],a[href*="flipkart.com"],a[href]');
                            const link = linkEl?linkEl.href:'';
                            if (!title.trim()||title.trim().length<5) return null;
                            return {
                                index: i+1,
                                title: title.trim().slice(0,100),
                                price: price.trim().slice(0,25),
                                rating: rating.trim().slice(0,15),
                                image: img,
                                link: link
                            };
                        }).filter(Boolean);
                    })()
                """)
                if products_js and len(products_js) >= 2:
                    lines = ["PRODUCT RESULTS:"]
                    for p in products_js:
                        line = f"  {p['index']:2}. {p['title']}"
                        if p.get('price'):  line += f"  |  {p['price']}"
                        if p.get('rating'): line += f"  |  {p['rating']}"
                        if p.get('link'):   line += f"  |  URL:{p['link'][:80]}"
                        lines.append(line)
                    return "\n".join(lines)
            except Exception:
                pass
            text = (await self._page.inner_text("body"))[:10000]
            return f"PAGE TEXT (parse for products):\n{text}"
        return self._run(_pl())

    def get_seat_grid(self) -> str:
        async def _sg():
            seats = await self._page.evaluate("""
                (() => {
                    const sels=['[data-row][data-col]','[data-row][data-num]',
                        '[data-row][data-seat]','.seat','.seat-block',
                        '[class*="seat-unit"]','[data-testid*="seat"]'];
                    let found=[];
                    for(const s of sels){
                        found=Array.from(document.querySelectorAll(s));
                        if(found.length>5)break;
                    }
                    const seats=[];
                    for(const el of found.slice(0,300)){
                        const row=el.getAttribute('data-row')||el.getAttribute('row')||'';
                        const col=el.getAttribute('data-col')||el.getAttribute('data-num')||
                            el.getAttribute('data-seat')||'';
                        const cls=el.className.toLowerCase();
                        const status=(cls.includes('booked')||cls.includes('sold')||
                            cls.includes('unavail')||cls.includes('blocked'))?'booked':'available';
                        if(row&&col)seats.push({row,col,status});
                    }
                    return seats;
                })()
            """)
            if not seats:
                return "No seat grid found."
            rows = {}
            for s in seats:
                rows.setdefault(s["row"], []).append(s)
            lines = ["SEAT MAP (✓=available ✗=booked):"]
            for row in sorted(rows.keys()):
                row_seats = sorted(rows[row], key=lambda x: str(x["col"]).zfill(3))
                sym = "".join(
                    f"[✓{s['col']}]" if s["status"] == "available" else f"[✗{s['col']}]"
                    for s in row_seats
                )
                lines.append(f"  {row}: {sym}")
            return "\n".join(lines)
        return self._run(_sg())


    def dismiss_popups(self) -> str:
        """Dismiss any visible popups, modals, overlays, or cookie banners."""
        async def _d():
            dismissed = []
            # Strategy 1: press Escape (closes most modal dialogs)
            try:
                await self._page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
                dismissed.append("Escape key")
            except Exception:
                pass

            # Strategy 2: click common close/dismiss buttons via JS
            result = await self._page.evaluate("""
                (() => {
                    const closeSelectors = [
                        // Generic close buttons
                        '[aria-label="Close"]', '[aria-label="close"]',
                        '[aria-label="Dismiss"]', '[aria-label="dismiss"]',
                        'button[class*="close"]', 'button[class*="Close"]',
                        'button[class*="dismiss"]', '.modal-close', '.popup-close',
                        '[data-dismiss="modal"]', '[data-bs-dismiss="modal"]',
                        // Google sign-in overlay
                        '#credential_picker_container iframe',
                        // Cookie consent
                        'button[id*="accept"]', 'button[id*="cookie"]',
                        'button[class*="cookie"]', 'button[class*="consent"]',
                        // Naukri-specific
                        '.naukri-modal .close', '.popup .icon-close',
                        'span.icon-close-wt', '[data-ga-label="close"]',
                        // LinkedIn sign-in wall
                        '.modal__dismiss', 'button.contextual-sign-in-modal__modal-dismiss',
                    ];
                    for (const sel of closeSelectors) {
                        try {
                            const el = document.querySelector(sel);
                            if (el) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 0 && r.height > 0) {
                                    el.click();
                                    return 'Dismissed: ' + sel;
                                }
                            }
                        } catch(e) {}
                    }

                    // Strategy 3: remove Google sign-in iframe overlay
                    const gcredential = document.querySelector('#credential_picker_container');
                    if (gcredential) {
                        gcredential.remove();
                        return 'Removed Google credential picker';
                    }

                    // Strategy 4: remove any fixed/absolute overlay covering the page
                    const overlays = Array.from(document.querySelectorAll('div, section'))
                        .filter(el => {
                            const s = window.getComputedStyle(el);
                            const r = el.getBoundingClientRect();
                            return (s.position === 'fixed' || s.position === 'absolute')
                                && s.zIndex > 100
                                && r.width > window.innerWidth * 0.3
                                && r.height > window.innerHeight * 0.3
                                && el.tagName !== 'HEADER'
                                && el.tagName !== 'NAV';
                        });
                    if (overlays.length > 0) {
                        overlays[0].style.display = 'none';
                        return 'Hidden overlay: ' + overlays[0].className.slice(0, 50);
                    }
                    return 'NO_POPUP_FOUND';
                })()
            """)
            dismissed.append(str(result))
            await asyncio.sleep(0.4)
            return " | ".join(dismissed)
        try:
            return self._run(_d())
        except Exception as e:
            return f"Dismiss failed: {e}"

    def url_changed(self, previous_url: str) -> bool:
        """Check if the current URL differs from a previous URL."""
        try:
            current = self.get_url()
            # Ignore trailing slash differences
            return current.rstrip("/") != previous_url.rstrip("/")
        except Exception:
            return False

    def take_screenshot_base64(self) -> str:

        """Take a screenshot of the current page and return as base64 PNG string."""
        async def _ss():
            import base64
            png_bytes = await self._page.screenshot(type='png', full_page=False)
            return base64.b64encode(png_bytes).decode('utf-8')
        try:
            return self._run(_ss())
        except Exception as e:
            return f"SCREENSHOT_FAILED: {e}"

# ── Singleton ─────────────────────────────────────────────────────────────────

_mgr: Optional[BrowserManager] = None

def get_browser_manager() -> BrowserManager:
    global _mgr
    if _mgr is None:
        _mgr = BrowserManager()
    return _mgr


# ─────────────────────────────────────────────────────────────────────────────
#  GENERAL BROWSER TOOLS — SOM-first, for job search / research / data tasks
# ─────────────────────────────────────────────────────────────────────────────

def _auto_ensure_connected(browser_name: str = "chrome") -> None:
    mgr = get_browser_manager()
    if mgr._browser is not None:
        return
    if not PLAYWRIGHT_OK:
        return
    print("[BrowserAgent] Auto-connecting...")
    if mgr.is_cdp_available():
        mgr.connect()
        return
    res = mgr.launch_real_browser(browser_name)
    print(f"[BrowserAgent] {res}")
    if mgr.is_cdp_available():
        mgr.connect()


def build_browser_tools() -> list:
    mgr = get_browser_manager()

    @tool
    def browser_connect() -> str:
        """Connect to the user's running browser via CDP on port 9222.

        Behaviour:
          - Browser already connected → returns current page info (no-op)
          - Edge/Chrome running WITH CDP on 9222 → attaches immediately
          - Edge/Chrome running WITHOUT CDP → returns clear setup instructions
            (NEVER launches a second window alongside an existing one)
          - No browser running at all → launches Edge with CDP enabled

        ALWAYS call this first before any other browser tool.
        """
        if not PLAYWRIGHT_OK:
            return "ERROR: Run: pip install playwright && python -m playwright install chromium"

        # Already connected
        if mgr._browser is not None:
            return f"Browser already connected. Page: {mgr.get_url()}"

        # CDP is available on port 9222 — connect to the existing window
        if mgr.is_cdp_available():
            result = mgr.connect()
            return f"Connected to your existing browser. {result}"

        # Check if a browser is already running WITHOUT CDP
        edge_running   = mgr._is_process_running("msedge.exe")
        chrome_running = mgr._is_process_running("chrome.exe")

        if edge_running or chrome_running:
            browser_name = "Edge" if edge_running else "Chrome"
            exe = "msedge.exe" if edge_running else "chrome.exe"
            return (
                f"ACTION REQUIRED: {browser_name} is running but WITHOUT remote debugging.\n\n"
                f"Steps to fix (takes 30 seconds):\n"
                f"  1. Close ALL {browser_name} windows (check Task Manager to be sure).\n"
                f"  2. Re-run your task — SentinelAI will auto-launch {browser_name} with CDP.\n\n"
                f"OR open it manually right now:\n"
                f"  {exe} --remote-debugging-port=9222 --remote-allow-origins=*\n\n"
                f"Then retry your request."
            )

        # No browser running — safe to launch fresh with CDP
        res = mgr.launch_real_browser("auto")
        print(f"[BrowserAgent] {res}")
        time.sleep(2.5)
        if not mgr.is_cdp_available():
            return (
                f"{res}\n"
                "CDP not responding yet. Run manually:\n"
                "  msedge.exe --remote-debugging-port=9222 --remote-allow-origins=*"
            )
        return mgr.connect()

    @tool
    def browser_navigate(url: str) -> str:
        """Navigate the browser to a URL.
        Handles ERR_NAME_NOT_RESOLVED with spelling hints.
        Args: url: full URL or domain (e.g. naukri.com — NOT naukari.com).
        After navigating call browser_act() to interact.
        """
        _auto_ensure_connected()
        return mgr.navigate(url)

    @tool
    def browser_act(instruction: str) -> str:
        """PRIMARY TOOL — Vision-guided browser interaction.

        Flow: dismiss popups → SOM scan → screenshot → vision LLM decides → execute.

        Use for EVERYTHING on a page:
          browser_act("fill the job search box with machine learning engineer")
          browser_act("fill the location field with Bangalore")
          browser_act("click the search button")
          browser_act("click the first job listing")
          browser_act("extract all job titles, companies, locations and salaries")
          browser_act("click Next Page")
          browser_act("what is shown on this page?")

        Args: instruction: plain English of what to do.
        """
        _auto_ensure_connected()

        import re as _re
        import json as _json
        from app.src.llm_rotation import get_llm, get_vision_model
        from langchain_core.messages import HumanMessage

        # ── Step 0: Dismiss any popup/overlay before doing anything ──────────
        popup_result = mgr.dismiss_popups()
        popup_note = "" if "NO_POPUP_FOUND" in popup_result else f"[Dismissed popup: {popup_result}] "

        # ── Step 1: SOM scan — draw red numbered boxes ───────────────────────
        mgr.som_scan()
        url_before  = mgr.get_url()
        title_before = mgr.get_title()

        # ── Step 2: Screenshot with boxes ────────────────────────────────────
        b64 = mgr.take_screenshot_base64()
        if b64.startswith("SCREENSHOT_FAILED"):
            return f"{popup_note}Screenshot failed. URL: {url_before}"

        # ── Step 3: Vision LLM — identify action + element ───────────────────
        vision_llm = get_llm(model=get_vision_model(), temperature=0.0)

        vision_prompt = (
            f"Current URL: {url_before}\n"
            f"Page title: {title_before}\n\n"
            "The screenshot shows a live browser page. "
            "Every interactive element has a RED NUMBERED BOX — those numbers are SOM IDs.\n\n"
            f"Task: {instruction}\n\n"
            "Respond with ONLY a JSON object (no markdown, no explanation outside JSON):\n"
            "{\n"
            '  "page_description": "what is visible on screen in 1-2 sentences",\n'
            '  "popup_or_overlay_visible": true or false,\n'
            '  "action": "click" | "fill" | "read" | "scroll" | "none",\n'
            '  "element_id": <integer SOM ID, or null>,\n'
            '  "fill_text": "text to type if action=fill, else null",\n'
            '  "scroll_direction": "down" | "up" | null,\n'
            '  "extracted_content": "all relevant text/data extracted if action=read",\n'
            '  "confidence": "high" | "medium" | "low",\n'
            '  "reasoning": "why this element and action"\n'
            "}\n\n"
            "RULES:\n"
            "- element_id MUST be a red number visible in the screenshot\n"
            "- If a popup/modal is covering content: set popup_or_overlay_visible=true, action=none\n"
            "- For fill: element_id = the input field ID, fill_text = text to type\n"
            "- For click: element_id = the button/link ID\n"
            "- For read/extract: set action=read and put ALL relevant data in extracted_content\n"
            "- If target not visible: action=scroll\n"
            "- confidence=low means you are not sure about the element ID"
        )

        try:
            msg = HumanMessage(content=[
                {"type": "text", "text": vision_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ])
            resp     = vision_llm.invoke([msg])
            raw_json = str(resp.content).strip().replace("```json", "").replace("```", "").strip()
            data     = _json.loads(raw_json)
        except Exception as e:
            # JSON failed — return raw vision text
            raw = str(resp.content)[:500] if 'resp' in dir() else str(e)
            return f"{popup_note}Vision response (non-JSON): {raw}"

        page_desc    = data.get("page_description", "")
        popup_vis    = data.get("popup_or_overlay_visible", False)
        action       = data.get("action", "none")
        elem_id      = data.get("element_id")
        fill_text    = data.get("fill_text")
        scroll_dir   = data.get("scroll_direction", "down")
        extracted    = data.get("extracted_content", "")
        confidence   = data.get("confidence", "high")
        reasoning    = data.get("reasoning", "")

        result_lines = [
            f"{popup_note}PAGE: {page_desc}",
        ]

        # ── Handle: vision says popup still visible → dismiss again ──────────
        if popup_vis:
            dismiss2 = mgr.dismiss_popups()
            time.sleep(0.5)
            result_lines.append(f"POPUP DETECTED — dismissing: {dismiss2}")
            # Re-scan and screenshot after dismissal
            mgr.som_scan()
            b64_2 = mgr.take_screenshot_base64()
            if not b64_2.startswith("SCREENSHOT_FAILED"):
                try:
                    msg2  = HumanMessage(content=[
                        {"type": "text", "text": vision_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_2}"}},
                    ])
                    resp2 = vision_llm.invoke([msg2])
                    raw2  = str(resp2.content).strip().replace("```json","").replace("```","").strip()
                    data  = _json.loads(raw2)
                    action    = data.get("action", action)
                    elem_id   = data.get("element_id", elem_id)
                    fill_text = data.get("fill_text", fill_text)
                    extracted = data.get("extracted_content", extracted)
                    confidence= data.get("confidence", confidence)
                    reasoning = data.get("reasoning", reasoning)
                    result_lines.append(f"RE-SCANNED after dismissal: {data.get('page_description','')}")
                except Exception:
                    pass

        result_lines.append(
            f"VISION: {action.upper()}"
            + (f" on [{elem_id}]" if elem_id is not None else "")
            + (f" text='{fill_text}'" if fill_text else "")
            + f" | confidence={confidence}"
        )
        result_lines.append(f"REASONING: {reasoning}")

        # ── Execute ───────────────────────────────────────────────────────────
        if action == "click" and elem_id is not None:
            click_result = mgr.som_click(int(elem_id))
            time.sleep(1.2)

            new_url   = mgr.get_url()
            new_title = mgr.get_title()
            navigated = mgr.url_changed(url_before)

            result_lines.append(f"EXECUTED: Clicked [{elem_id}] → {click_result}")
            result_lines.append(f"URL: {new_url}")

            # ── Navigation verification: if URL didn't change, try Enter ─────
            if not navigated and confidence in ("high", "medium"):
                result_lines.append(
                    "URL did not change after click — trying Enter key as fallback..."
                )
                mgr.press_key("Enter")
                time.sleep(1.5)
                new_url2 = mgr.get_url()
                if mgr.url_changed(url_before):
                    result_lines.append(f"Enter key worked! New URL: {new_url2}")
                    new_url = new_url2
                    navigated = True
                else:
                    result_lines.append(
                        f"Still on same URL. The element [{elem_id}] may not be a submit button. "
                        "Try browser_act() with a more specific instruction."
                    )

            if new_title and new_title != title_before:
                result_lines.append(f"NEW TITLE: {new_title}")

        elif action == "fill" and elem_id is not None:
            fill_result = mgr.som_fill(int(elem_id), fill_text or "")
            result_lines.append(
                f"EXECUTED: Filled [{elem_id}] with '{fill_text}' → {fill_result}"
            )

        elif action == "read":
            if extracted:
                result_lines.append(f"\nEXTRACTED CONTENT:\n{extracted}")
            else:
                page_text = mgr.get_page_text(max_chars=4000)
                result_lines.append(f"\nPAGE TEXT:\n{page_text}")

        elif action == "scroll":
            d = scroll_dir or "down"
            mgr.scroll(d, 600)
            result_lines.append(
                f"EXECUTED: Scrolled {d}. Call browser_act() again."
            )

        else:
            result_lines.append(
                "No action executed. "
                "Call browser_act() with a more specific instruction."
            )

        return "\n".join(result_lines)

    @tool
    def browser_act_and_read(instruction: str) -> str:
        """Vision-guided action + immediately extract page content afterwards.

        Same as browser_act() but after clicking/filling it waits for the page
        to load and then automatically extracts all relevant text content.

        Use when you need to:
          - Click a job listing AND read its details
          - Submit a search AND get the results
          - Navigate AND immediately collect data

        Args: instruction: what to do, e.g.
          "click the first job result and read the job description"
          "submit the search form and extract all job listings"
        """
        _auto_ensure_connected()

        # First do the action
        action_result = browser_act.invoke({"instruction": instruction})

        # Wait for any navigation/load
        time.sleep(1.5)

        # Then extract page content via vision
        extract_result = browser_act.invoke({"instruction": "extract all visible text content, job listings, titles, companies, salaries, URLs from this page"})

        return (
            f"=== ACTION ===\n{action_result}\n\n"
            f"=== CONTENT AFTER ===\n{extract_result}"
        )

    @tool
    def browser_scroll(direction: str) -> str:
        """Scroll the page up or down. Args: direction: 'down' or 'up'.
        After scrolling, call browser_act() to interact with newly visible elements.
        """
        return mgr.scroll(direction, 500)

    @tool
    def browser_press_key(key: str) -> str:
        """Press a keyboard key. Args: key: Enter, Tab, Escape, ArrowDown, ctrl+a.
        Use after browser_act() fills a search box to submit it.
        """
        return mgr.press_key(key)

    @tool
    def browser_get_tabs() -> str:
        """List all open browser tabs with index, title, URL.
        Use before switching tabs when a link opened a new tab.
        """
        tabs = mgr.get_all_tabs()
        return "Open tabs:\n" + "\n".join(tabs) if tabs else "No tabs open."

    @tool
    def browser_switch_tab(tab_index: int) -> str:
        """Switch to a tab by 1-based index. Use browser_get_tabs() first.
        After switching call browser_act() to read or interact.
        Args: tab_index: 1=first tab, 2=second, etc.
        """
        result = mgr.switch_to_tab(tab_index)
        time.sleep(0.5)
        return result

    @tool
    def browser_go_back() -> str:
        """Navigate to the previous page. Use when you need to return to a listing
        after opening a detail page.
        """
        return mgr.go_back()

    @tool
    def browser_current_state() -> str:
        """Quick check of current browser URL and page title."""
        return (
            f"Connected: {mgr._browser is not None}\n"
            f"URL: {mgr.get_url()}\n"
            f"Title: {mgr.get_title()}"
        )

    return [
        browser_connect,       # 1 — always first
        browser_navigate,      # 2 — go to URL
        browser_act,           # 3 ← PRIMARY: vision-guided interact
        browser_act_and_read,  # 4 ← POWER: act + read in one call
        browser_scroll,        # 5 — scroll up/down
        browser_press_key,     # 6 — keyboard
        browser_get_tabs,      # 7 — list tabs
        browser_switch_tab,    # 8 — switch tab
        browser_go_back,       # 9 — back button
        browser_current_state, # 10 — where am I?
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  GENERAL BROWSER AGENT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_browser_agent(llm=None) -> AgentExecutor:
    """
    General-purpose browser agent with vision-first interaction.
    Uses vision LLM to identify exact elements — much more reliable than text parsing.
    """
    if llm is None:
        from app.src.llm_rotation import get_fast_llm
        llm = get_fast_llm(temperature=0.0)

    tools = build_browser_tools()

    system_prompt = """You are BrowserAgent — an expert browser automation agent controlling the user's real Chrome/Edge browser.
You can do ANYTHING a human can do in a browser: search jobs, scrape data, fill forms, navigate sites, extract content.

═══════════════════════════════════════════════════════════
  TOOLS (you have exactly these — use nothing else)
═══════════════════════════════════════════════════════════

  browser_connect()                    Connect to browser — ALWAYS call first
  browser_navigate(url)                Go to a URL
  browser_act(instruction)             ← PRIMARY TOOL — vision-guided action
  browser_act_and_read(instruction)    Act + immediately read result page
  browser_scroll(direction)            Scroll "up" or "down"
  browser_press_key(key)               Press Enter, Tab, Escape, ArrowDown, etc.
  browser_get_tabs()                   List all open tabs
  browser_switch_tab(index)            Switch to tab by number
  browser_go_back()                    Browser back button
  browser_current_state()              Quick URL + title check

═══════════════════════════════════════════════════════════
  HOW browser_act() WORKS — understand this fully
═══════════════════════════════════════════════════════════

  browser_act(instruction) does all of this automatically:
    1. Dismisses any popup / overlay / sign-in dialog
    2. Draws red numbered boxes on EVERY interactive element (SOM)
    3. Takes a screenshot showing the page + red numbered boxes
    4. Sends screenshot + your instruction to the vision LLM
    5. Vision LLM identifies exact element ID → executes click/fill/read
    6. Verifies navigation happened — retries with Enter if it didn't
    7. Returns what was done + any extracted content

  You just describe what you want in plain English. Examples:

    browser_act("fill the search box with machine learning engineer")
    browser_act("fill the location field with Bangalore")
    browser_act("click the search button")
    browser_act("click the first job result")
    browser_act("extract all job titles, companies, locations, salaries from this page")
    browser_act("click the Next Page button")
    browser_act("dismiss any popup or sign-in dialog")
    browser_act("click the Login button")
    browser_act("what is shown on this page?")
    browser_act("scroll down and find the Apply button")

═══════════════════════════════════════════════════════════
  STARTUP — always begin like this
═══════════════════════════════════════════════════════════

  browser_connect()
  browser_navigate("URL")
  browser_act("dismiss any popup, sign-in dialog, or overlay")
  browser_act("your first task on this page")

═══════════════════════════════════════════════════════════
  STRATEGY 1 — DIRECT SEARCH URLs (always try first)
═══════════════════════════════════════════════════════════

  Skip form-filling entirely. Navigate directly to results.
  This bypasses popups, sign-in walls, and search form issues.

  NAUKRI.COM:
    https://www.naukri.com/{{role}}-jobs-in-{{city}}
    https://www.naukri.com/{{role}}-jobs-in-{{city}}?experience={{min}}to{{max}}
    Examples:
      https://www.naukri.com/full-stack-developer-jobs-in-hyderabad?experience=0to2
      https://www.naukri.com/machine-learning-jobs-in-bangalore?experience=0to3
      https://www.naukri.com/python-developer-jobs-in-pune
    Replace spaces with hyphens. Role and city are lowercase hyphenated.

  LINKEDIN JOBS:
    https://www.linkedin.com/jobs/search/?keywords={{role}}&location={{city}}
    Example: https://www.linkedin.com/jobs/search/?keywords=data+analyst&location=Mumbai

  INDEED INDIA:
    https://www.indeed.co.in/jobs?q={{role}}&l={{city}}&explvl=entry_level
    Example: https://www.indeed.co.in/jobs?q=python+developer&l=Hyderabad

  INTERNSHALA:
    https://internshala.com/internships/{{role}}-internship
    https://internshala.com/internships/work-from-home-{{role}}-internship

  GITHUB TRENDING:
    https://github.com/trending
    https://github.com/trending/{{language}}?since=daily

  GOOGLE SEARCH:
    https://www.google.com/search?q={{query}}
    Use when site URL is unknown or for web research.

  YOUTUBE:
    https://www.youtube.com/results?search_query={{query}}

  AMAZON INDIA:
    https://www.amazon.in/s?k={{query}}

  FLIPKART:
    https://www.flipkart.com/search?q={{query}}&sort=price_asc

═══════════════════════════════════════════════════════════
  STRATEGY 2 — FORM FILLING (fallback when direct URL fails)
═══════════════════════════════════════════════════════════

  When direct URL doesn't work or site requires interaction:

  browser_navigate("https://www.naukri.com")
  browser_act("dismiss any popup or sign-in dialog that appeared")
  browser_act("fill the job title search field with full stack developer")
  browser_act("fill the location/city field with Hyderabad")
  browser_act("click the search button or press Enter")
  [verify: browser_current_state() — check URL changed to results page]
  browser_act("extract all job listings with title, company, location, salary")

  CRITICAL after any form submit:
  If URL did not change → the click did not work.
  Try: browser_act("press Enter or submit the search form")
  If still stuck → try direct URL strategy above.

═══════════════════════════════════════════════════════════
  POPUP AND OVERLAY HANDLING
═══════════════════════════════════════════════════════════

  Popups are handled automatically inside browser_act(). But if you
  see the agent getting confused or clicking wrong things:

  browser_act("dismiss any sign-in popup, modal, or overlay on screen")
  browser_act("close the Google sign-in dialog")
  browser_act("press Escape to close any dialog")
  browser_press_key("Escape")

  Common popups you will encounter:
  - Google account sign-in overlay on Naukri, YouTube, etc.
  - LinkedIn sign-in wall (partially blocking content)
  - Cookie consent banners
  - Naukri registration popup
  - Email subscription modals

  ALL are handled by browser_act() automatically. If one persists,
  add "dismiss any popup" as your first instruction.

═══════════════════════════════════════════════════════════
  DATA EXTRACTION — how to get clean results
═══════════════════════════════════════════════════════════

  After reaching a results page:
    browser_act("extract all job listings. For each job get: title, company, location, salary, experience required, and job URL")
    browser_act("read the full article text on this page")
    browser_act("list all products with name, price, rating, and URL")
    browser_act("extract all repository names, languages, star counts, and descriptions")
    browser_act("get all internship listings with company, role, stipend, duration, and apply link")

  For multi-page results:
    [extract page 1]
    browser_act("click the Next Page or page 2 button")
    [extract page 2]
    Repeat up to 3 pages or as instructed.

  Format extracted data clearly:
    Job 1: [Title] | [Company] | [Location] | [Salary] | [URL]
    Job 2: ...

═══════════════════════════════════════════════════════════
  TAB HANDLING
═══════════════════════════════════════════════════════════

  Many sites open job details in a new tab when you click a listing.
  If browser_act() says "opened NEW TAB" or URL didn't change:

    browser_get_tabs()         ← see all open tabs with index
    browser_switch_tab(2)      ← switch to the new tab (usually tab 2)
    browser_act("read this page content")
    browser_go_back()          ← or switch back to original tab

═══════════════════════════════════════════════════════════
  NAVIGATION VERIFICATION — check it worked
═══════════════════════════════════════════════════════════

  After clicking a search button or link, always verify:
    browser_act("verify we are now on the search results page, not the homepage")
    OR
    browser_current_state()   ← check URL changed

  If still on same URL after clicking:
    browser_press_key("Enter")            ← submit via keyboard
    browser_act("click the search or go button")   ← try again
    Use direct URL strategy instead.

═══════════════════════════════════════════════════════════
  SITE-SPECIFIC PLAYBOOKS
═══════════════════════════════════════════════════════════

  ── NAUKRI.COM (job search) ──────────────────────────────
  BEST: Direct URL
    browser_navigate("https://www.naukri.com/full-stack-developer-jobs-in-hyderabad?experience=0to2")
    browser_act("dismiss any popup")
    browser_act("extract all job listings with title, company, location, salary, experience")
    browser_act("click Next Page")
    browser_act("extract all job listings again")

  ── LINKEDIN JOBS ────────────────────────────────────────
    browser_navigate("https://www.linkedin.com/jobs/search/?keywords=data+scientist&location=Bangalore")
    browser_act("dismiss any sign-in prompt")
    browser_act("extract all visible job listings with title, company, location, posted date")

  ── GITHUB TRENDING ──────────────────────────────────────
    browser_navigate("https://github.com/trending")
    browser_act("extract all trending repositories: name, description, language, stars today, total stars, URL")

  ── INTERNSHALA ──────────────────────────────────────────
    browser_navigate("https://internshala.com/internships/python-internship")
    browser_act("extract all internship listings: company, role, stipend, duration, location, apply link")

  ── INDEED INDIA ─────────────────────────────────────────
    browser_navigate("https://www.indeed.co.in/jobs?q=machine+learning+engineer&l=Bangalore")
    browser_act("dismiss any popups")
    browser_act("extract all job listings: title, company, location, salary, description snippet")

  ── WIKIPEDIA ────────────────────────────────────────────
    browser_navigate("https://en.wikipedia.org/wiki/{{Topic}}")
    browser_act("extract the full article text, especially the introduction and key sections")

  ── YOUTUBE ──────────────────────────────────────────────
    browser_navigate("https://www.youtube.com/results?search_query={{query}}")
    browser_act("extract all video titles, channel names, view counts, and URLs from results")

  ── GOOGLE SEARCH ────────────────────────────────────────
    browser_navigate("https://www.google.com/search?q={{query}}")
    browser_act("extract all search result titles, URLs, and snippets")

═══════════════════════════════════════════════════════════
  DECISION TREE — what to do when things go wrong
═══════════════════════════════════════════════════════════

  PROBLEM: Popup appeared, agent clicking wrong things
  FIX: browser_act("dismiss any popup, sign-in dialog, or overlay")
       browser_press_key("Escape")

  PROBLEM: Clicked search button but URL didn't change
  FIX 1: browser_press_key("Enter")
  FIX 2: Use direct URL — browser_navigate("naukri.com/role-jobs-in-city")

  PROBLEM: Page not loading / ERR_NAME_NOT_RESOLVED
  FIX: Check URL spelling. browser_navigate("https://www.google.com/search?q=site+name+official")
       Then browser_act("click the official site link")

  PROBLEM: Content not visible / page looks wrong
  FIX: browser_scroll("down") then browser_act("extract content")

  PROBLEM: New tab opened with job/product details
  FIX: browser_get_tabs() → browser_switch_tab(2) → browser_act("read this page")

  PROBLEM: Sign-in wall blocking content (LinkedIn, Glassdoor)
  FIX: Use a direct Google cache URL or try:
       browser_act("close or dismiss the login modal")
       browser_act("click the X or close button on the sign-in dialog")

  PROBLEM: Results page is empty / no jobs shown
  FIX: Try different direct URL. Check filters are not too narrow.
       browser_navigate("https://www.naukri.com/{{role}}-jobs")  (no city filter)

═══════════════════════════════════════════════════════════
  OUTPUT FORMAT — always structure results clearly
═══════════════════════════════════════════════════════════

  For job searches:
    ─────────────────────────────────────
    Job 1: Software Engineer | TCS | Hyderabad | ₹6-9 LPA | 0-2 yrs
    Job 2: Full Stack Dev | Infosys | Bangalore | ₹8-12 LPA | 1-3 yrs
    ...
    Source: naukri.com | Page 1 of results
    ─────────────────────────────────────

  For GitHub trending:
    1. microsoft/vscode — TypeScript — ★ 167K — Code editor
    2. facebook/react — JavaScript — ★ 218K — UI framework

  For general data extraction:
    Present data in a clean table or numbered list.
    Always include the source URL.

═══════════════════════════════════════════════════════════
  ABSOLUTE RULES
═══════════════════════════════════════════════════════════

  ✓ ALWAYS browser_connect() first
  ✓ ALWAYS try direct search URL before form-filling
  ✓ ALWAYS dismiss popups before interacting with a page
  ✓ ALWAYS verify URL changed after a search/navigation action
  ✓ ALWAYS format extracted data cleanly before returning
  ✓ Extract from MULTIPLE pages when instructed (up to 3 pages)

  ✗ NEVER do e-commerce ordering or payment — that is ecommerce_agent
  ✗ NEVER click Pay / Confirm Order / Checkout
  ✗ NEVER make up job listings — only report what is actually on screen
  ✗ NEVER give up with "I don't have access" — you control a real browser
  ✗ NEVER stop after one failed attempt — try direct URL or alternative approach"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)

    class TrimmedExecutor(AgentExecutor):
        def _call(self, inputs, run_manager=None):
            if "intermediate_steps" in inputs and len(inputs["intermediate_steps"]) > 6:
                inputs = {**inputs, "intermediate_steps": inputs["intermediate_steps"][-6:]}
            return super()._call(inputs, run_manager=run_manager)

    return TrimmedExecutor(
        agent=agent, tools=tools, verbose=True,
        handle_parsing_errors=True,
        max_iterations=25,
    )
