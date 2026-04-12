"""
test_buy_now_button.py  —  INTERACTIVE PICKER
==============================================
Connects to your real browser via CDP, finds every element with
"buy now" text, lists them numbered, then lets you type a number
to ACTUALLY CLICK that element.

Usage:
    python test_buy_now_button.py "https://www.flipkart.com/your-product-url"
"""

import sys
import asyncio
import json
import urllib.request
from playwright.async_api import async_playwright

CDP_URL = "http://localhost:9222"


def _check_cdp() -> bool:
    try:
        req = urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2)
        return req.status == 200
    except Exception:
        return False


async def main(product_url: str):
    if not _check_cdp():
        print("❌ CDP not reachable on port 9222.")
        print("   msedge.exe --remote-debugging-port=9222 --remote-allow-origins=*")
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        page    = context.pages[0] if context.pages else await context.new_page()

        print(f"\n[NAV] Going to product page …")
        await page.goto(product_url, wait_until="networkidle", timeout=40_000)
        await page.wait_for_timeout(1500)

        print("[SCROLL] Triggering sticky bar …")
        await page.evaluate("window.scrollTo({top: 800, behavior: 'smooth'})")
        await page.wait_for_timeout(1500)

        # ── Find all "buy now / buy at" elements ──────────────────────────
        raw = await page.evaluate("""
        (() => {
          const results = [];
          for (const el of document.querySelectorAll('*')) {
            if (el.children.length > 8) continue;
            const raw = (el.innerText || el.textContent || '').trim();
            if (!raw) continue;
            const tl = raw.toLowerCase();
            if (!tl.includes('buy now') && !tl.includes('buy at ')) continue;
            if (raw.length > 150) continue;
            const r   = el.getBoundingClientRect();
            const pos = window.getComputedStyle(el).position;
            results.push({
              tag:  el.tagName,
              text: raw.replace(/\\n/g,' ').slice(0, 70),
              x:    Math.round(r.left + r.width  / 2),
              y:    Math.round(r.top  + r.height / 2),
              w:    Math.round(r.width),
              h:    Math.round(r.height),
              pos:  pos,
              cls:  (el.className||'').toString().slice(0,80),
            });
          }
          results.sort((a,b) => (b.w*b.h) - (a.w*a.h));
          return JSON.stringify(results);
        })()
        """)
        candidates = json.loads(raw or "[]")

        if not candidates:
            print("\n❌ No 'buy now' elements found in DOM.")
            await browser.close()
            return

        # ── Print numbered list ───────────────────────────────────────────
        print(f"\n{'='*65}")
        print(f"  Found {len(candidates)} candidate(s). Pick a number to click it.")
        print(f"{'='*65}")
        for i, c in enumerate(candidates, 1):
            print(f"  [{i:2}]  \"{c['text']}\"")
            print(f"        <{c['tag']}>  {c['w']}x{c['h']}  ({c['x']},{c['y']})  pos={c['pos']}")
            print(f"        class: {c['cls'] or '(none)'}")
            print()

        # ── User picks ────────────────────────────────────────────────────
        while True:
            raw_input = input("Enter number to click (or 'q' to quit): ").strip()
            if raw_input.lower() == 'q':
                print("Exiting without clicking.")
                break

            try:
                choice = int(raw_input)
                if choice < 1 or choice > len(candidates):
                    print(f"  Please enter a number between 1 and {len(candidates)}.")
                    continue
            except ValueError:
                print("  Invalid input — enter a number.")
                continue

            target = candidates[choice - 1]
            cx, cy = target["x"], target["y"]
            print(f"\n[CLICK] Clicking [{choice}] \"{target['text']}\" at ({cx},{cy}) …")

            if cx <= 0 or cy <= 0:
                print("  ⚠ Coords are off-screen. Trying JS click instead …")
                # JS click by index among all matching elements
                result = await page.evaluate(f"""
                (() => {{
                  let idx = 0;
                  for (const el of document.querySelectorAll('*')) {{
                    if (el.children.length > 8) continue;
                    const raw = (el.innerText||el.textContent||'').trim();
                    const tl  = raw.toLowerCase();
                    if (!tl.includes('buy now') && !tl.includes('buy at ')) continue;
                    if (raw.length > 150) continue;
                    idx++;
                    if (idx === {choice}) {{
                      el.scrollIntoView({{behavior:'instant',block:'center'}});
                      ['mousedown','mouseup','click'].forEach(ev =>
                        el.dispatchEvent(new MouseEvent(ev,{{bubbles:true,cancelable:true,view:window}}))
                      );
                      return 'JS_CLICK:' + raw.slice(0,60);
                    }}
                  }}
                  return 'NOT_FOUND';
                }})()
                """)
                print(f"  JS click result: {result}")
            else:
                await page.mouse.click(cx, cy)
                print(f"  ✅ Mouse clicked at ({cx},{cy})")

            # Wait and report
            await page.wait_for_timeout(2500)
            new_url = page.url
            print(f"\n  URL after click: {new_url}")

            checkout_keywords = ["checkout","viewcheckout","cart","payment","address","place-order","buy-now","order-summary"]
            if any(k in new_url.lower() for k in checkout_keywords):
                print("  ✅ CHECKOUT REACHED — this is the right element!")
                print(f"\n  ✔ Winning class: '{target['cls']}'")
                print(f"  ✔ Winning text:  '{target['text']}'")
            else:
                print("  Page didn't navigate to checkout. Try a different number.")

            again = input("\nClick another number? (y/n): ").strip().lower()
            if again != 'y':
                break

        await browser.close()
        print("\nDone.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python test_buy_now_button.py "<flipkart-url>"')
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))