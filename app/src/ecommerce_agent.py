"""
ecommerce_agent.py — E-Commerce Agent for SentinelAI v2

Uses SOM (Set-of-Marks) as the primary interaction model.
SOM draws red numbered boxes on ALL interactive elements —
reliable across Flipkart, Amazon, Swiggy, Zomato, BookMyShow, IRCTC etc.
regardless of DOM class changes or React re-renders.

USE THIS AGENT FOR:
  ✓ Shopping (Flipkart, Amazon, Myntra, Meesho, Snapdeal)
  ✓ Food ordering (Swiggy, Zomato, Blinkit)
  ✓ Travel / tickets (IRCTC, MakeMyTrip, EaseMyTrip, redBus)
  ✓ Movie / event booking (BookMyShow)
  ✓ Checkout flows with address/payment pages (stops before payment)

STOP RULE: NEVER click Pay / Confirm Order / Place Order.
Always hand over to the user at the payment step.

WORKFLOW:
  navigate → ec_get_products → STOP → user picks → 
  ec_som_scan → ec_som_click product → ec_som_scan product page →
  ec_som_click Buy Now → hand over at checkout
"""

from __future__ import annotations

import re
import threading
import time
from typing import Optional

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

# ── SHARED BrowserManager singleton ──────────────────────────────────────────
# CRITICAL FIX: ecommerce_agent MUST share the same BrowserManager instance
# as browser_agent.  Creating a second BrowserManager causes two separate CDP
# connections which each try to launch their own Edge window — hence the
# "multiple Edge windows" bug seen in the logs.
from app.src.agents.browser_agent import get_browser_manager

def get_ecommerce_manager():
    """Return the shared BrowserManager singleton (same as browser_agent uses)."""
    return get_browser_manager()


# ─────────────────────────────────────────────────────────────────────────────
#  Flipkart product text parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_flipkart_text(text: str) -> list[dict]:
    """
    Parse Flipkart/Amazon search page text into structured product list.
    Works directly on page text — no DOM class guessing.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    products = []

    # Skip nav/header section
    start = 0
    for j, line in enumerate(lines):
        if "Showing" in line and "results" in line:
            start = j + 1
            break
    lines = lines[start:]

    i = 0
    idx = 1
    while i < len(lines) and len(products) < 20:
        line = lines[i]
        if (len(line) > 10 and
                not line.startswith("₹") and
                not re.match(r"^[\d.,]+$", line) and
                line not in ["Bank Offer", "Hot Deal", "Super Deals", "Not deliverable",
                             "Only few left", "NEXT", "Sort By", "Filters"] and
                not re.match(r"^\d+\s*\(", line) and
                not re.match(r"^Page \d", line) and
                not line.startswith("http") and
                "off" not in line and
                "%" not in line):

            title = line
            price = ""
            orig_price = ""
            discount = ""
            rating = ""
            reviews = ""
            skip_lines = 0

            for j in range(i + 1, min(i + 6, len(lines))):
                nxt = lines[j]
                rating_match = re.match(r"^(\d+\.?\d*)\s*\(([0-9,]+)\)$", nxt)
                if rating_match and not price:
                    rating = rating_match.group(1)
                    reviews = rating_match.group(2)
                    continue
                price_match = re.match(r"₹([\d,]+)₹([\d,]+)(\d+%\s*off)", nxt)
                if price_match:
                    price = price_match.group(1)
                    orig_price = price_match.group(2)
                    discount = price_match.group(3)
                    skip_lines = j - i
                    break
                simple_price = re.match(r"^₹([\d,]+)$", nxt)
                if simple_price and not price:
                    price = simple_price.group(1)
                    skip_lines = j - i

            if price:
                products.append({
                    "idx": idx,
                    "title": title[:90],
                    "price": price,
                    "orig": orig_price,
                    "disc": discount,
                    "rating": rating,
                    "reviews": reviews,
                })
                idx += 1
                i += max(skip_lines, 1) + 1
                continue
        i += 1

    return products


def _format_products(products: list[dict]) -> str:
    if not products:
        return ""
    lines = [f"PRODUCTS ({len(products)} found — prices from live page):"]
    lines.append("=" * 60)
    for p in products:
        lines.append(f"\n[{p['idx']}] {p['title']}")
        price_line = f"    Price: ₹{p['price']}"
        if p.get("orig"):
            price_line += f"  (was ₹{p['orig']})"
        if p.get("disc"):
            price_line += f"  {p['disc']}"
        lines.append(price_line)
        if p.get("rating"):
            rev = f" ({p['reviews']} reviews)" if p.get("reviews") else ""
            lines.append(f"    Rating: {p['rating']}★{rev}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  TOOLS
# ─────────────────────────────────────────────────────────────────────────────

def build_ecommerce_tools() -> list:
    mgr = get_ecommerce_manager()

    def _ensure():
        if mgr._browser is None:
            if mgr.is_cdp_available():
                mgr.connect()

    # ── 1. Connect ────────────────────────────────────────────────────────────

    @tool
    def ec_connect() -> str:
        """Connect to the user's running browser via CDP.
        - If Edge or Chrome is already open with CDP → connects directly (no restart)
        - If no browser with CDP → launches Edge with a separate CDP profile
        - NEVER closes or kills existing browser windows
        ALWAYS call this first.
        """
        if mgr._browser is not None:
            return f"Already connected. Page: {mgr.get_url()}"
        if mgr.is_cdp_available():
            result = mgr.connect()
            print(f"[ec_connect] CDP available, connected: {result[:80]}")
            return result
        # No CDP available — launch browser with CDP enabled
        print("[ec_connect] No CDP on port 9222. Launching browser...")
        res = mgr.launch_real_browser("auto")
        print(f"[ec_connect] Launch result: {res}")
        # Wait and retry CDP check a few times
        import time as _t
        for attempt in range(4):
            _t.sleep(1.5)
            if mgr.is_cdp_available():
                result = mgr.connect()
                print(f"[ec_connect] Connected on attempt {attempt+1}: {result[:80]}")
                return result
            print(f"[ec_connect] CDP not ready yet (attempt {attempt+1}/4)...")
        return (
            f"{res}\n"
            "CDP not responding after launch. Your existing browser may be blocking CDP.\n"
            "Fix: Close ALL Edge windows, then reopen Edge or let SentinelAI open it.\n"
            "  msedge.exe --remote-debugging-port=9222 --remote-allow-origins=*"
        )

    # ── 2. Navigate ───────────────────────────────────────────────────────────

    @tool
    def ec_navigate(url: str) -> str:
        """Navigate to a URL. Use pre-filtered search URLs for best results.
        Flipkart: https://www.flipkart.com/search?q=QUERY&sort=price_asc&max=PRICE
        Amazon:   https://www.amazon.in/s?k=QUERY
        Swiggy:   https://www.swiggy.com
        """
        _ensure()
        return mgr.navigate(url)

    # ── 3. SOM Scan — PRIMARY interaction tool ────────────────────────────────

    @tool
    def ec_som_scan(query: str = "") -> str:
        """PRIMARY TOOL — Scan page and draw red numbered boxes on ALL interactive elements.
        Returns [ID:N] list. ALWAYS call after every navigate, click, or page change.
        Args: query: optional filter e.g. 'buy now', 'add to cart', 'checkout'.
        After scanning: use ec_som_click(N) or ec_som_fill(N, text).
        """
        _ensure()
        return mgr.som_scan(query)

    # ── 4. SOM Click ──────────────────────────────────────────────────────────

    @tool
    def ec_som_click(element_id) -> str:
        """Click an element by its SOM ID from ec_som_scan().
        Most reliable click — works on any element regardless of class changes.
        Args: element_id: the N from [ID:N] in ec_som_scan() output.
        After clicking: call ec_som_scan() again to see the updated page.
        """
        try:
            element_id = int(element_id)
        except (ValueError, TypeError):
            return f"element_id must be a number, got: {element_id!r}"
        _ensure()
        return mgr.som_click(element_id)

    # ── 5. SOM Fill ───────────────────────────────────────────────────────────

    @tool
    def ec_som_fill(element_id, text: str) -> str:
        """Type text into a field by its SOM ID from ec_som_scan().
        Has JS fallback for React/Angular inputs (Flipkart, Amazon, Swiggy).
        Args: element_id: the N from [ID:N] in ec_som_scan() output.
        """
        try:
            element_id = int(element_id)
        except (ValueError, TypeError):
            return f"element_id must be a number, got: {element_id!r}"
        _ensure()
        return mgr.som_fill(element_id, text)

    # ── 6. Get Products ───────────────────────────────────────────────────────

    @tool
    def ec_get_products() -> str:
        """Parse the current search results page and return a numbered product list.
        Extracts prices, ratings, discounts directly from page text — accurate.

        CRITICAL after calling this:
        → Show ALL products to the user.
        → Say: 'Please type the NUMBER of the product you want to order.'
        → STOP. Do NOT call any more tools until user replies with a number.
        """
        _ensure()
        text = mgr.get_page_text(max_chars=8000)
        products = _parse_flipkart_text(text)

        if not products:
            return (
                "Could not parse product list from page text.\n"
                f"Raw page text (first 2000 chars):\n{text[:2000]}"
            )

        result = _format_products(products)
        result += (
            "\n\n" + "━" * 60 + "\n"
            "AWAITING_USER_SELECTION\n"
            "Show the numbered list above to the user.\n"
            "Say: 'Please type the NUMBER of the product you want to order.'\n"
            "STOP — do NOT call any more tools until user replies.\n"
            + "━" * 60
        )
        return result

    # ── 7. Page text ──────────────────────────────────────────────────────────

    @tool
    def ec_get_page_text() -> str:
        """Read all visible text from the current page (up to 6000 chars).
        Use to read product title, price, delivery info, availability, ratings.
        """
        _ensure()
        return mgr.get_page_text()

    # ── 8. Add to Cart / Buy Now — site-aware with vision fallback ──────────────

    @tool
    def ec_add_to_cart() -> str:
        """Click Buy Now / Add to Cart on the current product page.

        Strategy order (tried in sequence until one works):
          1. Site-specific CSS selectors (Flipkart / Amazon / Myntra) — exact hardcoded
          2. Generic JS text-scan (Buy Now / Add to Cart by visible text)
          3. SOM scan filtered by buy/cart keywords
          4. Scroll down + retry JS scan
          5. Screenshot → vision LLM analysis (Swiggy, Zomato, BookMyShow, etc.)

        Call only when on the PRODUCT PAGE (URL has /p/ or /dp/).
        Returns CHECKOUT REACHED when done — STOP and hand over to user.
        NEVER click Pay / Confirm Order.
        """
        import time

        def _check_checkout(url: str) -> bool:
            u = url.lower()
            return any(k in u for k in [
                "viewcheckout", "checkout", "cart", "order-summary",
                "place-order", "buy-now", "payment", "address"
            ])

        def _success(label: str, url: str) -> str:
            return (
                f"SUCCESS via {label}.\n"
                f"CHECKOUT REACHED: {url}\n"
                "AGENT: STOP. Tell user: "
                "'Your item is ready at checkout. Please complete payment in the browser.'"
            )

        current_url = mgr.get_url().lower()

        # ── STRATEGY 1: Site-specific hardcoded selectors ─────────────────────
        site_selectors = []  # list of (label, css_selector, action_type)

        if "flipkart.com" in current_url:
            site_selectors = [
                (
                    "flipkart-buy-now-grid",
                    (
                        "#slot-list-container > div.lQLKCP > "
                        "div.fWi7J_:nth-of-type(2) > div > div.yiQOTv > "
                        "div.CTTtEa > div.OmE16y:nth-of-type(1) > div.CTTtEa > "
                        "div.OmE16y:nth-of-type(2) > div.CTTtEa > "
                        "div.OmE16y:nth-of-type(16) > "
                        "div.asbjxx.A02XR3.lV7ANv.Yd5OMU > div > "
                        "div.css-g5y9jx.r-13awgt0.r-eqz5dr > div > div > div > "
                        "div._1psv1zeb9._1psv1ze0 > div.css-g5y9jx > "
                        "div._1psv1zeb9._1psv1ze0._7dzyg20._1psv1ze9l._1psv1ze7o."
                        "_1psv1ze2u._1psv1ze53 > div.css-g5y9jx > "
                        "div.grid-formation.grid-column-2:nth-of-type(2) > "
                        "div._1psv1zeb9._1psv1ze0._1psv1zeku._1psv1ze6r > "
                        "div > div.css-g5y9jx > div.css-g5y9jx:nth-of-type(2)"
                    ),
                    "buy_now"
                ),
                ("flipkart-buy-now-class",   "._2KpZ6l",  "buy_now"),
                ("flipkart-add-cart-class",  "._3v1vjh",  "add_cart"),
            ]

        elif "amazon.in" in current_url or "amazon.com" in current_url:
            site_selectors = [
                ("amazon-buy-now",      "#buy-now-button",      "buy_now"),
                ("amazon-add-to-cart",  "#add-to-cart-button",  "add_cart"),
                ("amazon-submit-buy",   "#submit.buyNow",       "buy_now"),
            ]

        elif "myntra.com" in current_url:
            site_selectors = [
                (
                    "myntra-buy-now",
                    (
                        "#mountRoot > div > div:nth-of-type(1) > "
                        "main.pdp-pdp-container > "
                        "div.pdp-details.common-clearfix:nth-of-type(2) > "
                        "div.pdp-description-container:nth-of-type(2) > "
                        "div:nth-of-type(2) > div:nth-of-type(3) > "
                        "div.pdp-action-container.pdp-fixed > "
                        "div.pdp-add-to-bag.pdp-button.pdp-flex.pdp-center:nth-of-type(1)"
                    ),
                    "buy_now"
                ),
                ("myntra-add-bag", ".pdp-add-to-bag", "add_cart"),
            ]

        for label, selector, action_type in site_selectors:
            try:
                # Escape single quotes in selector for JS embedding
                sel_escaped = selector.replace("'", "\\'")
                result = mgr.execute_js(
                    f"(() => {{"
                    f"  const el = document.querySelector('{sel_escaped}');"
                    f"  if (!el) return 'NOT_FOUND';"
                    f"  const r = el.getBoundingClientRect();"
                    f"  if (r.width === 0 && r.height === 0) return 'HIDDEN';"
                    f"  el.scrollIntoView({{behavior:'instant',block:'center'}});"
                    f"  el.click();"
                    f"  return 'CLICKED: ' + (el.textContent||el.value||'').trim().slice(0,50);"
                    f"}})()"
                )
                if result and "CLICKED" in result:
                    time.sleep(2.2)
                    url = mgr.get_url()
                    if _check_checkout(url):
                        return _success(label, url)
                    if action_type == "add_cart":
                        return (
                            f"OK [{label}]: {result}. URL: {url}\n"
                            "Item added to cart. Navigate to cart to checkout:\n"
                            "  Flipkart: ec_navigate('https://www.flipkart.com/viewcart')\n"
                            "  Amazon:   ec_navigate('https://www.amazon.in/gp/cart/view.html')"
                        )
                    return f"OK [{label}]: {result}. URL: {url}"
            except Exception:
                continue

        # ── STRATEGY 2: Generic JS text-scan ─────────────────────────────────
        buy_js = (
            "(() => {"
            + "const buy=['buy now','buy at','proceed to pay','place order'];"
            + "const cart=['add to cart','add to bag','add to basket'];"
            + "const all=document.querySelectorAll("
            + "  'button,a,div[role=button],span[role=button],input[type=submit]');"
            + "for(const el of all){"
            + "  const t=(el.textContent||el.value||'').trim().toLowerCase();"
            + "  if(buy.some(b=>t===b||t.startsWith(b))){"
            + "    el.scrollIntoView({behavior:'instant',block:'center'});el.click();"
            + "    return 'BUY_CLICKED: '+el.textContent.trim().slice(0,50);"
            + "  }"
            + "}"
            + "for(const el of all){"
            + "  const t=(el.textContent||el.value||'').trim().toLowerCase();"
            + "  if(cart.some(c=>t===c||t.includes(c))){"
            + "    el.scrollIntoView({behavior:'instant',block:'center'});el.click();"
            + "    return 'CART_CLICKED: '+el.textContent.trim().slice(0,50);"
            + "  }"
            + "}"
            + "return 'NOT_FOUND';"
            + "})()"
        )
        r2 = mgr.execute_js(buy_js)
        if r2 and "CLICKED" in r2:
            time.sleep(2.0)
            url = mgr.get_url()
            if _check_checkout(url):
                return _success("JS-text-scan", url)
            return f"OK JS-text-scan: {r2}. URL: {url}"

        # ── STRATEGY 3: SOM scan filtered by buy/cart keywords ───────────────
        som = mgr.som_scan("buy")
        for line in som.split("\n"):
            low = line.lower()
            if any(k in low for k in ["buy now", "add to cart", "buy at", "add to bag"]):
                m = re.search(r"\[ID:(\d+)\]", line)
                if m:
                    cr = mgr.som_click(int(m.group(1)))
                    time.sleep(2.0)
                    url = mgr.get_url()
                    if _check_checkout(url):
                        return _success("SOM-click", url)
                    return f"OK SOM-click: {cr}. URL: {url}"

        # ── STRATEGY 4: Scroll down + retry JS scan ───────────────────────────
        mgr.scroll("down", 400)
        time.sleep(0.5)
        r4 = mgr.execute_js(
            "(() => {"
            + "for(const el of document.querySelectorAll('button,a')){"
            + "const t=el.textContent.trim().toLowerCase();"
            + "if((t.includes('add')&&t.includes('cart'))||t==='buy now'||t.startsWith('buy at')){"
            + "el.scrollIntoView({behavior:'instant',block:'center'});el.click();"
            + "return 'SCROLL_CLICK: '+t.slice(0,40);"
            + "}} return 'NOT_FOUND';})()"
        )
        if r4 and "CLICK" in r4:
            time.sleep(2.0)
            url = mgr.get_url()
            if _check_checkout(url):
                return _success("scroll-retry", url)
            return f"OK scroll+retry: {r4}. URL: {url}"

        # ── STRATEGY 5: Screenshot → Vision LLM (Swiggy/Zomato/complex sites) ──
        # All other sites (or when CSS/JS strategies failed): take a screenshot,
        # send to the vision model, and let it identify exactly what to click next.
        try:
            b64 = mgr.take_screenshot_base64()
            if b64 and not b64.startswith("SCREENSHOT_FAILED"):
                from app.src.llm_rotation import get_llm, get_vision_model
                from langchain_core.messages import HumanMessage

                vision_llm = get_llm(model=get_vision_model(), temperature=0.0)
                url_now = mgr.get_url()
                site_match = re.search(r"https?://([^/]+)", url_now)
                site_name = site_match.group(1) if site_match else "this page"

                msg = HumanMessage(content=[
                    {
                        "type": "text",
                        "text": (
                            f"You are automating a purchase on {site_name}.\n"
                            f"Current URL: {url_now}\n\n"
                            "Look at this screenshot carefully and answer:\n"
                            "1. What page is this? (product page, menu, cart, etc.)\n"
                            "2. What is the EXACT text of the button to click to add to cart "
                            "   or proceed with ordering?\n"
                            "3. What red SOM number box is nearest to that button? "
                            "   (Look for small red numbered overlays on elements)\n"
                            "4. Are there any required steps before clicking buy "
                            "   (e.g. select size, select variant, login)?\n\n"
                            "Reply in this format:\n"
                            "PAGE: <type> | BUTTON_TEXT: <exact text> | SOM_ID: <number or UNKNOWN> | PRE_STEPS: <none or description>"
                        )
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}
                    }
                ])

                vision_resp = vision_llm.invoke([msg])
                vision_text = str(vision_resp.content).strip()

                # Extract SOM ID from vision response
                som_id_m = re.search(
                    r"SOM[_\s]?ID[:\s]+([0-9]+)|click\s+(?:number\s+)?([0-9]+)",
                    vision_text, re.IGNORECASE
                )
                if som_id_m:
                    vid = int(som_id_m.group(1) or som_id_m.group(2))
                    cr = mgr.som_click(vid)
                    time.sleep(2.0)
                    url = mgr.get_url()
                    if _check_checkout(url):
                        return _success(f"vision-LLM(SOM:{vid})", url)
                    return (
                        f"Vision-guided click on SOM:{vid}.\n"
                        f"Click result: {cr}\n"
                        f"URL: {url}\n"
                        f"Vision said: {vision_text[:250]}\n"
                        "NEXT: Call ec_som_scan() to see updated page."
                    )

                # Vision responded but couldn't identify a SOM ID — return for agent
                return (
                    f"VISION_ANALYSIS — {site_name}:\n{vision_text}\n\n"
                    "NEXT STEPS:\n"
                    "1. Call ec_som_scan() to get all current element IDs\n"
                    "2. Match the button text from the vision analysis to a SOM ID\n"
                    "3. Call ec_som_click(N) to click it\n"
                    "   OR call ec_vision_next_step() for more detailed guidance"
                )
        except Exception:
            pass

        return (
            f"Could not find Buy Now / Add to Cart on {mgr.get_url()[:60]}.\n"
            "Possible reasons:\n"
            "  - Not on a product page yet (check URL for /p/ or /dp/)\n"
            "  - Size / colour / variant selection required first\n"
            "  - Food delivery: need to browse menu and add items\n"
            "Try:\n"
            "  ec_som_scan()           — scan all visible elements\n"
            "  ec_scroll('down', 400)  — reveal hidden buttons\n"
            "  ec_vision_next_step()   — let vision LLM guide the next step"
        )

    # ── 8b. Vision Next Step — screenshot + LLM for complex flows ────────────

    @tool
    def ec_vision_next_step(question: str = "") -> str:
        """Take a screenshot of the current browser page and ask the vision LLM what to do next.

        Use this for:
          - Swiggy / Zomato — restaurant browsing, menu navigation, add items to cart
          - BookMyShow — show listing, time slot, seat selection
          - IRCTC — train search, passenger form, booking flow
          - Any site where ec_add_to_cart() returned VISION_ANALYSIS
          - When the page layout is unclear and SOM alone is not enough

        Args:
            question: Optional specific question. Default: guidance for next step.

        Returns: Vision analysis + recommended SOM ID to click.
        After: call ec_som_scan() then ec_som_click(recommended_id).
        """
        _ensure()
        try:
            b64 = mgr.take_screenshot_base64()
            if b64.startswith("SCREENSHOT_FAILED"):
                return f"Screenshot failed: {b64}"

            from app.src.llm_rotation import get_llm, get_vision_model
            from langchain_core.messages import HumanMessage

            vision_llm = get_llm(model=get_vision_model(), temperature=0.0)
            url = mgr.get_url()
            site_m = re.search(r"https?://([^/]+)", url)
            site_name = site_m.group(1) if site_m else url

            user_q = question.strip() or (
                "What should I click next to proceed with my order or booking? "
                "Identify the exact button text and its SOM ID (red numbered box)."
            )

            msg = HumanMessage(content=[
                {
                    "type": "text",
                    "text": (
                        f"You are a browser automation assistant on {site_name}.\n"
                        f"Current URL: {url}\n\n"
                        f"Task: {user_q}\n\n"
                        "The red numbered overlays on the page are SOM IDs for automation.\n\n"
                        "Please answer:\n"
                        "PAGE_TYPE: What kind of page is this?\n"
                        "CURRENT_STATE: What has already happened / what is visible?\n"
                        "NEXT_ACTION: What exact button/element to click or fill?\n"
                        "ELEMENT_TEXT: Exact text of the element\n"
                        "SOM_ID: The red number box closest to that element (integer or UNKNOWN)\n"
                        "EXTRA_STEPS: Any steps needed before or after (e.g. fill address, select date)"
                    )
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"}
                }
            ])

            response = vision_llm.invoke([msg])
            vision_text = str(response.content).strip()

            # Parse SOM ID
            som_m = re.search(
                r"SOM[_\s]?ID[:\s]+([0-9]+)|click\s+(?:ID\s+|number\s+)?([0-9]+)",
                vision_text, re.IGNORECASE
            )
            recommended = som_m.group(1) or som_m.group(2) if som_m else None

            lines = [
                f"=== VISION ANALYSIS: {site_name} ===",
                vision_text,
            ]
            if recommended:
                lines.append(f"\nRECOMMENDED ACTION: ec_som_click({recommended})")
            else:
                lines.append(
                    "\nNo SOM ID found. "
                    "Call ec_som_scan() and match element text from analysis above."
                )
            return "\n".join(lines)

        except Exception as e:
            return (
                f"Vision LLM error: {e}\n"
                "Fallback: call ec_som_scan() to see all interactive elements."
            )

    # ── 9. Scroll ─────────────────────────────────────────────────────────────

    @tool
    def ec_scroll(direction: str = "down", px: int = 400) -> str:
        """Scroll the page to reveal more content or buttons.
        After scrolling: call ec_som_scan() to see newly revealed elements.
        Args: direction: 'down' or 'up', px: pixels.
        """
        _ensure()
        return mgr.scroll(direction, px)

    # ── 10. Page key ──────────────────────────────────────────────────────────

    @tool
    def ec_press_key(key: str) -> str:
        """Press a keyboard key. Args: key: Enter, Tab, Escape.
        Use after filling address fields to submit or move to next field.
        """
        _ensure()
        return mgr.press_key(key)

    # ── 11. Tab management ────────────────────────────────────────────────────

    @tool
    def ec_get_tabs() -> str:
        """List all open browser tabs with index, title, URL.
        Flipkart opens products in new tabs — use this to find them.
        """
        _ensure()
        tabs = mgr.get_all_tabs()
        return "Open tabs:\n" + "\n".join(tabs) if tabs else "No tabs found."

    @tool
    def ec_switch_tab(tab_index: int) -> str:
        """Switch to a browser tab by 1-based index.
        After switching: call ec_som_scan() to read the new tab.
        Args: tab_index: 1=first tab, 2=second, etc.
        """
        _ensure()
        return mgr.switch_to_tab(tab_index)

    # ── 12. Wait ──────────────────────────────────────────────────────────────

    @tool
    def ec_wait_for_url(fragment: str, timeout_sec: int = 10) -> str:
        """Wait for URL to contain a fragment — confirms navigation.
        Args: fragment: e.g. 'checkout', 'payment', '/p/', '/dp/'.
        """
        _ensure()
        return mgr.wait_for_url(fragment, timeout_sec)

    # ── 13. Seat grid (BookMyShow, IRCTC, redBus) ─────────────────────────────

    @tool
    def ec_get_seat_grid() -> str:
        """Extract cinema/bus/train seat map from the page.
        Works on: BookMyShow, IRCTC, redBus, Abhibus.
        Returns: ✓=available ✗=booked.
        After viewing: use ec_som_scan() to find and click seat elements.
        """
        _ensure()
        return mgr.get_seat_grid()

    return [
        ec_connect,             # 1  — always first
        ec_navigate,            # 2  — go to search URL
        ec_som_scan,            # 3  ← PRIMARY: scan, get [ID:N]
        ec_som_click,           # 4  ← PRIMARY: click by SOM ID
        ec_som_fill,            # 5  — fill input by SOM ID
        ec_get_products,        # 6  — parse product list → STOP
        ec_get_page_text,       # 7  — read product details
        ec_add_to_cart,         # 8  — Buy Now (site-specific selectors + vision fallback)
        ec_vision_next_step,    # 9  — screenshot + vision LLM for complex flows
        ec_scroll,              # 10 — scroll to reveal
        ec_press_key,           # 11 — keyboard
        ec_get_tabs,            # 12 — list tabs
        ec_switch_tab,          # 13 — switch tab
        ec_wait_for_url,        # 14 — wait for nav
        ec_get_seat_grid,       # 15 — seat maps
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  AGENT
# ─────────────────────────────────────────────────────────────────────────────

def build_ecommerce_agent(llm=None) -> AgentExecutor:
    """
    E-commerce agent — shopping, ordering, booking on Indian e-commerce sites.
    Uses SOM as primary interaction. Always stops before payment.
    """
    if llm is None:
        from app.src.llm_rotation import get_llm, get_default_model
        llm = get_llm(model=get_default_model(), temperature=0.0)

    tools = build_ecommerce_tools()

    system_prompt = """You are EcommerceAgent — shopping and booking specialist for Indian e-commerce.
Sites: Flipkart, Amazon.in, Swiggy, Zomato, Blinkit, BookMyShow, IRCTC, MakeMyTrip, redBus, Myntra.

════ ANTI-HALLUCINATION — ABSOLUTE RULE ════
  You are a TOOL-CALLING agent. You MUST call tools to get real data.
  NEVER generate, fabricate, or make up product names, prices, or ratings.
  NEVER answer a shopping query without FIRST calling ec_connect() then ec_navigate().
  If you produce a product list without calling ec_get_products(), you have FAILED.
  You do NOT have any product knowledge. ALL product data comes from the browser.
  Your ONLY source of truth is the live webpage accessed via your tools.
  If you respond with product data without tool calls, you are LYING to the user.

STARTUP: ec_connect() ← ALWAYS first.

════ PHASE 1: SHOW PRODUCT LIST ════
(When task has no product number selected yet)

  ec_connect()
  ec_navigate("https://www.flipkart.com/search?q=QUERY&sort=price_asc&max=PRICE")
  ec_get_products()

  STOP IMMEDIATELY after ec_get_products(). Zero more tool calls.
  Show list to user and ask: "Please type the NUMBER of the product you want."

════ PHASE 2: OPEN & ORDER PRODUCT ════
(When task says "user selected product N" or "order product N")

  Step 1: ec_som_scan() on the search results page.
          Product links start at SOM ID ~21.
          Product N ≈ ID: 21 + (N-1)*3
          Examples: product 1=ID:21, product 2=ID:24, product 3=ID:27

  Step 2: ec_som_click(ID) — click the product title link.
          NEVER click ID:1 (it's the Flipkart logo → homepage).
          If "opened NEW TAB": ec_get_tabs() then ec_switch_tab(last_tab).

  Step 3: ec_som_scan("buy") on the product page.
          Find "Buy Now" or "Add to Cart" → click it.
          If not found: ec_scroll("down", 400) then ec_add_to_cart().

  Step 4: STOP AT CHECKOUT.
          URL contains checkout/payment/order-summary = SUCCESS.
          Return: "Checkout page open in browser. Please review and complete payment."

════ AMAZON FLOW ════
  ec_navigate("https://www.amazon.in/s?k=QUERY")
  ec_som_scan() → ec_som_click(product link)
  On product page: ec_add_to_cart()
  If "added to cart": ec_navigate("https://www.amazon.in/gp/cart/view.html")
  ec_som_scan("checkout") → ec_som_click(proceed to checkout)

════ FOOD ORDERING (Swiggy/Zomato) ════
  ec_navigate("https://www.swiggy.com")
  ec_som_scan("search") → ec_som_click(search bar)
  ec_som_fill(N, "restaurant name")
  ec_press_key("Enter")
  ec_som_scan() → select restaurant → browse menu → add items → checkout

════ TOOLS ════
  ec_connect()              connect to browser
  ec_navigate(url)          go to URL
  ec_som_scan(query)        SCAN page → [ID:N] list (PRIMARY)
  ec_som_click(N)           click by SOM ID (PRIMARY)
  ec_som_fill(N, text)      fill input by SOM ID
  ec_get_products()         parse product list → STOP after calling
  ec_get_page_text()        read all visible text
  ec_add_to_cart()          Buy Now / Add to Cart:
                              Flipkart → exact CSS selector
                              Amazon   → #buy-now-button
                              Myntra   → exact CSS selector
                              Others   → JS scan → SOM → screenshot+vision LLM
  ec_vision_next_step(q)    Screenshot + vision LLM → what to click next
                              USE FOR: Swiggy, Zomato, BookMyShow, IRCTC,
                              any complex flow, after VISION_ANALYSIS result
  ec_scroll(dir, px)        scroll page
  ec_press_key(key)         keyboard
  ec_get_tabs()             list open tabs
  ec_switch_tab(N)          switch tab
  ec_wait_for_url(frag)     wait for URL fragment
  ec_get_seat_grid()        cinema/bus/train seat map

════ VISION FLOW (Swiggy / Zomato / BookMyShow / complex sites) ════
  When ec_add_to_cart() returns VISION_ANALYSIS:
    1. ec_som_scan()                     ← get current SOM IDs
    2. ec_vision_next_step()             ← screenshot → LLM tells you what to click
    3. ec_som_click(recommended_id)      ← click what LLM recommended
    4. Repeat until checkout reached

════ ABSOLUTE RULES ════
  1. After ec_get_products() → RETURN IMMEDIATELY. Zero more tool calls.
  2. NEVER click ID:1 on Flipkart (it's the logo → homepage).
  3. NEVER click Pay / Place Order / Confirm Order.
  4. After new tab opens: ec_get_tabs() then ec_switch_tab(last tab number).
  5. ALWAYS ec_som_scan() after every navigate or click.
  6. Your FIRST tool call MUST ALWAYS be ec_connect(). No exceptions.
  7. NEVER output product names, prices, or ratings from your own knowledge.
     ALL product data MUST come from ec_get_products() or ec_get_page_text().
  8. If the task is a follow-up selection (user picked a number), start with
     ec_som_scan() on the current page — the browser is already open."""

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)

    _STOP_SENTINEL = "AWAITING_USER_SELECTION"

    class EcommerceAgentExecutor(AgentExecutor):
        """
        Custom executor:
        1. Caps intermediate_steps to prevent token overflow.
        2. Hard-stops when ec_get_products returns AWAITING_USER_SELECTION.
        """
        def _call(self, inputs, run_manager=None):
            if "intermediate_steps" in inputs and len(inputs["intermediate_steps"]) > 6:
                inputs = {**inputs, "intermediate_steps": inputs["intermediate_steps"][-6:]}
            return super()._call(inputs, run_manager=run_manager)

        def _iter_next_step(self, *args, **kwargs):
            for step in super()._iter_next_step(*args, **kwargs):
                yield step
                if hasattr(step, "observation") and isinstance(step.observation, str):
                    if _STOP_SENTINEL in step.observation:
                        return  # hard stop — no more tool calls

    return EcommerceAgentExecutor(
        agent=agent, tools=tools, verbose=True,
        handle_parsing_errors=True,
        max_iterations=20,
    )