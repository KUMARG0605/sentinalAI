"""
test_prompts.py — SentinelAI v2 Agent Test Suite
═══════════════════════════════════════════════════════════════════════════════

QUICK REFERENCE — copy-paste any of these:

  python test_prompts.py --list                        # see all tests numbered
  python test_prompts.py --run 1                       # run test #1
  python test_prompts.py --run 3,7,12                  # run tests #3, #7, #12
  python test_prompts.py --run 1-5                     # run tests #1 through #5
  python test_prompts.py --group noui                  # all headless tests
  python test_prompts.py --group browser               # browser tests
  python test_prompts.py --group ecommerce             # ecommerce tests
  python test_prompts.py --group system                # system/desktop tests
  python test_prompts.py --group comms                 # WhatsApp/Telegram tests
  python test_prompts.py --agent file_agent            # tests using file_agent
  python test_prompts.py --agent research_agent        # tests using research_agent
  python test_prompts.py --agent ecommerce_agent       # tests using ecommerce_agent
  python test_prompts.py --agent browser_agent         # tests using browser_agent
  python test_prompts.py --mixed                       # only multi-agent tests (2+ agents)
  python test_prompts.py --mixed --group noui          # multi-agent headless only
  python test_prompts.py --all                         # all tests incl. browser/desktop
  python test_prompts.py --dry-run                     # print prompts, don't run agents
  python test_prompts.py --stop-on-fail                # stop at first failure
  python test_prompts.py --prompt "search youtube for cats"   # run a custom prompt
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────────────────────
#  TEST CASE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TestCase:
    prompt:  str
    group:   str
    note:    str       = ""
    agents:  list[str] = field(default_factory=list)   # agents expected to be used
    expect:  list[str] = field(default_factory=list)   # keywords expected in answer
    skip:    bool      = False                          # needs browser/desktop — skip by default


# ─────────────────────────────────────────────────────────────────────────────
#  ALL TESTS
# ─────────────────────────────────────────────────────────────────────────────

ALL_TESTS: list[TestCase] = [

    # ══════════════════════════════════════════════════════════════════════════
    #  UTILITY
    # ══════════════════════════════════════════════════════════════════════════

    TestCase(                                                                   # 1
        prompt="What is today's date and time?",
        group="utility",
        note="utility_agent — datetime",
        agents=["utility_agent"],
        expect=["202"],
    ),
    TestCase(                                                                   # 2
        prompt="Take a screenshot and save it to the Desktop as sentinel_test.png",
        group="utility",
        note="utility_agent + file_agent — screenshot + save",
        agents=["utility_agent", "file_agent"],
        skip=True,
    ),

    # ══════════════════════════════════════════════════════════════════════════
    #  RESEARCH
    # ══════════════════════════════════════════════════════════════════════════

    TestCase(                                                                   # 3
        prompt="What is the capital of France?",
        group="research",
        note="research_agent — Wikipedia fact lookup",
        agents=["research_agent"],
        expect=["paris"],
    ),
    TestCase(                                                                   # 4
        prompt="Search the web for the latest news about artificial intelligence today",
        group="research",
        note="research_agent — web search",
        agents=["research_agent"],
        expect=["ai", "artificial", "intelligence"],
    ),
    TestCase(                                                                   # 5
        prompt="What is the weather in Nellore right now?",
        group="research",
        note="research_agent — weather_search",
        agents=["research_agent"],
        expect=["weather", "nellore"],
    ),
    TestCase(                                                                   # 6
        prompt="Search YouTube for Python tutorial for beginners and list the top 5 videos",
        group="research",
        note="research_agent — youtube_search",
        agents=["research_agent"],
        expect=["youtube", "python", "tutorial"],
    ),
    TestCase(                                                                   # 7
        prompt="Find the 3 most recent research papers on large language models from ArXiv",
        group="research",
        note="research_agent — arxiv_search",
        agents=["research_agent"],
        expect=["arxiv", "language model"],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    #  FILE
    # ══════════════════════════════════════════════════════════════════════════

    TestCase(                                                                   # 8
        prompt=(
            "Create a file on the Desktop called sentinel_hello.txt "
            "with content: Hello from SentinelAI v2! This is a test."
        ),
        group="file",
        note="file_agent — create_file",
        agents=["file_agent"],
        expect=["sentinel_hello.txt", "created"],
    ),
    TestCase(                                                                   # 9
        prompt="Read the file sentinel_hello.txt from the Desktop and show me its content",
        group="file",
        note="file_agent — read_file",
        agents=["file_agent"],
        expect=["hello", "sentinelai"],
    ),
    TestCase(                                                                   # 10
        prompt=(
            "Create a CSV file on the Desktop called test_data.csv with columns: "
            "Name, Age, City. Add rows: Alice 25 Delhi, Bob 30 Mumbai, Carol 22 Bangalore"
        ),
        group="file",
        note="file_agent — write_csv",
        agents=["file_agent"],
        expect=["test_data", "created"],
    ),
    TestCase(                                                                   # 11
        prompt="List all files on my Desktop",
        group="file",
        note="file_agent — list_files",
        agents=["file_agent"],
        expect=["desktop"],
    ),
    TestCase(                                                                   # 12
        prompt="Search for all PDF files on my Desktop and Documents folder",
        group="file",
        note="file_agent — search_files PDFs",
        agents=["file_agent"],
        expect=["pdf", "desktop"],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    #  MEMORY
    # ══════════════════════════════════════════════════════════════════════════

    TestCase(                                                                   # 13
        prompt="What tasks have you done for me recently?",
        group="memory",
        note="orchestrator memory — recent task recall",
        agents=[],
        expect=["task", "recent"],
    ),
    TestCase(                                                                   # 14
        prompt="Summarize everything you know about what I have been working on",
        group="memory",
        note="orchestrator memory — work summary",
        agents=[],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    #  EDGE CASES
    # ══════════════════════════════════════════════════════════════════════════

    TestCase(                                                                   # 15
        prompt="xyzzy frobulate the quux",
        group="edge",
        note="edge — graceful handling of nonsense input",
        agents=[],
    ),
    TestCase(                                                                   # 16
        prompt="Go to naukari.com",
        group="edge",
        note="edge — browser_agent typo URL (naukari vs naukri)",
        agents=["browser_agent"],
        skip=True,
    ),

    # ══════════════════════════════════════════════════════════════════════════
    #  NOUI — headless multi-agent pipelines (no browser / desktop needed)
    # ══════════════════════════════════════════════════════════════════════════

    TestCase(                                                                   # 17
        prompt=(
            "Get today's date and time, search the web for the latest AI news, "
            "and save both to ai_news_today.txt on the Desktop."
        ),
        group="noui",
        note="utility + research + file — date + web news to file",
        agents=["utility_agent", "research_agent", "file_agent"],
        expect=["ai_news_today"],
    ),
    TestCase(                                                                   # 18
        prompt=(
            "Search Wikipedia for 'Machine Learning', then search ArXiv for the 3 latest "
            "transformer papers, and write everything to ml_research.txt on the Desktop."
        ),
        group="noui",
        note="research(wiki + arxiv) + file — ML research report",
        agents=["research_agent", "file_agent"],
        expect=["ml_research", "machine learning"],
    ),
    TestCase(                                                                   # 19
        prompt=(
            "What is the weather in Mumbai today? Also search the web for "
            "'best things to do in Mumbai'. Save both to mumbai_info.txt on the Desktop."
        ),
        group="noui",
        note="research(weather + web) + file — city guide",
        agents=["research_agent", "file_agent"],
        expect=["mumbai", "mumbai_info"],
    ),
    TestCase(                                                                   # 20
        prompt=(
            "Search YouTube for 'Python Django tutorial 2024' and get the top 5 links. "
            "Also search the web for 'best Django learning resources'. "
            "Save both to django_resources.txt on the Desktop."
        ),
        group="noui",
        note="research(youtube + web) + file — learning resources",
        agents=["research_agent", "file_agent"],
        expect=["django", "django_resources"],
    ),
    TestCase(                                                                   # 21
        prompt=(
            "Get today's date, search Wikipedia for a historical event that happened "
            "on this day, and save a summary to today_in_history.txt on the Desktop."
        ),
        group="noui",
        note="utility + research(wiki) + file — date-driven history",
        agents=["utility_agent", "research_agent", "file_agent"],
        expect=["today_in_history"],
    ),
    TestCase(                                                                   # 22
        prompt=(
            "Search the web for 'top 10 Python libraries for data science 2024', "
            "then create python_libraries.csv on the Desktop with columns: "
            "Name, Category, Description. Fill with the top 5 libraries."
        ),
        group="noui",
        note="research + file(csv) — web research to structured CSV",
        agents=["research_agent", "file_agent"],
        expect=["python_libraries.csv"],
    ),
    TestCase(                                                                   # 23
        prompt=(
            "Find the latest news about OpenAI from the past week and also "
            "look up the Wikipedia page for OpenAI. Write a 200-word combined "
            "report to openai_report.txt on the Desktop."
        ),
        group="noui",
        note="research(web + wiki) + file — multi-source report",
        agents=["research_agent", "file_agent"],
        expect=["openai_report", "openai"],
    ),
    TestCase(                                                                   # 24
        prompt=(
            "Get the current weather in Delhi, Mumbai, and Bangalore. "
            "Create weather_comparison.txt on the Desktop with a table: "
            "City, Temperature, Condition, Humidity."
        ),
        group="noui",
        note="research(3x weather parallel) + file — weather comparison",
        agents=["research_agent", "file_agent"],
        expect=["delhi", "mumbai", "bangalore", "weather_comparison"],
    ),
    TestCase(                                                                   # 25
        prompt=(
            "Look up 'Generative AI' on Wikipedia, search ArXiv for 5 recent papers, "
            "and check the weather in Hyderabad. Write a daily briefing to "
            "my_daily_briefing.txt with all three."
        ),
        group="noui",
        note="research(wiki + arxiv + weather) + file — full daily briefing",
        agents=["research_agent", "file_agent"],
        expect=["my_daily_briefing", "generative", "hyderabad"],
    ),
    TestCase(                                                                   # 26
        prompt=(
            "Search the web for 'best mechanical keyboards under 2000 rupees India 2024'. "
            "Save as keyboard_options.csv on the Desktop with columns: Product, Price, Rating, URL."
        ),
        group="noui",
        note="research + file(csv) — product research to CSV",
        agents=["research_agent", "file_agent"],
        expect=["keyboard_options"],
    ),
    TestCase(                                                                   # 27
        prompt=(
            "Search the web for 'remote Python developer jobs India 2024' and "
            "'Python developer salary India 2024'. Combine both into "
            "python_jobs_report.txt on the Desktop."
        ),
        group="noui",
        note="research(2x parallel web) + file — job + salary report",
        agents=["research_agent", "file_agent"],
        expect=["python_jobs_report", "salary"],
    ),
    TestCase(                                                                   # 28
        prompt=(
            "Get the current time, search YouTube for 'morning workout routine', "
            "and search the web for 'healthy breakfast ideas India'. "
            "Combine into morning_plan.txt on the Desktop."
        ),
        group="noui",
        note="utility + research(youtube + web) + file — morning wellness plan",
        agents=["utility_agent", "research_agent", "file_agent"],
        expect=["morning_plan", "workout"],
    ),
    TestCase(                                                                   # 29
        prompt=(
            "What tasks have you done for me recently? Based on those, "
            "suggest what I might want to do next and save the suggestions "
            "to next_tasks.txt on the Desktop."
        ),
        group="noui",
        note="memory + file — recall history and write suggestions",
        agents=["file_agent"],
        expect=["next_tasks"],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    #  BROWSER — needs Chrome open with --remote-debugging-port=9222
    # ══════════════════════════════════════════════════════════════════════════

    TestCase(                                                                   # 30
        prompt=(
            "Go to naukri.com and search for 'machine learning engineer' jobs in Bangalore. "
            "Get the first 10 listings with title, company, location. "
            "Save to ml_jobs_naukri.txt on the Desktop."
        ),
        group="browser",
        note="browser(naukri) + file — ML job scrape",
        agents=["browser_agent", "file_agent"],
        expect=["naukri", "ml_jobs_naukri"],
        skip=True,
    ),
    TestCase(                                                                   # 31
        prompt=(
            "Search for 'data analyst' jobs on LinkedIn in Mumbai. "
            "Also search the web for 'data analyst average salary Mumbai'. "
            "Combine both and save to data_analyst_jobs.txt on the Desktop."
        ),
        group="browser",
        note="browser(linkedin) + research(web) + file — jobs + salary",
        agents=["browser_agent", "research_agent", "file_agent"],
        expect=["data analyst", "data_analyst_jobs"],
        skip=True,
    ),
    TestCase(                                                                   # 32
        prompt=(
            "Go to github.com/trending and list the top 10 trending repositories. "
            "For each get: name, language, stars, description. "
            "Save to github_trending.txt on the Desktop. "
            "Also search the web for details about the #1 trending repo."
        ),
        group="browser",
        note="browser(github) + research(web) + file — trending repos",
        agents=["browser_agent", "research_agent", "file_agent"],
        expect=["github_trending", "trending"],
        skip=True,
    ),
    TestCase(                                                                   # 33
        prompt=(
            "Go to internshala.com and search for Python developer work-from-home internships. "
            "Get the top 5 with company name, stipend, duration. "
            "Save to python_internships.txt on the Desktop."
        ),
        group="browser",
        note="browser(internshala) + file — internship scrape",
        agents=["browser_agent", "file_agent"],
        expect=["internshala", "python_internships"],
        skip=True,
    ),
    TestCase(                                                                   # 34
        prompt=(
            "Search naukri.com for 'full stack developer' jobs in Hyderabad with 0-2 years experience. "
            "Get job titles, companies, salaries. "
            "Also search the web for average full stack developer salary in Hyderabad. "
            "Save a combined report to fullstack_jobs.txt on the Desktop."
        ),
        group="browser",
        note="browser(naukri) + research(web salary) + file — combined",
        agents=["browser_agent", "research_agent", "file_agent"],
        expect=["full stack", "fullstack_jobs"],
        skip=True,
    ),
    TestCase(                                                                   # 35
        prompt=(
            "Go to indeed.co.in and search for 'AI engineer' jobs in Bangalore. "
            "Scrape the first page. Also search Wikipedia for what an AI engineer does. "
            "Save both to ai_engineer_info.txt on the Desktop."
        ),
        group="browser",
        note="browser(indeed) + research(wiki) + file",
        agents=["browser_agent", "research_agent", "file_agent"],
        expect=["indeed", "ai_engineer_info"],
        skip=True,
    ),

    # ══════════════════════════════════════════════════════════════════════════
    #  ECOMMERCE — needs Chrome open with CDP
    # ══════════════════════════════════════════════════════════════════════════

    TestCase(                                                                   # 36
        prompt=(
            "Go to flipkart.com and search for mechanical keyboards under 2000 rupees "
            "sorted by price low to high. Show me the product list. "
            "Also search the web for reviews of the cheapest option. "
            "Save everything to keyboard_comparison.txt on the Desktop."
        ),
        group="ecommerce",
        note="ecommerce(flipkart) + research(review) + file",
        agents=["ecommerce_agent", "research_agent", "file_agent"],
        expect=["keyboard", "keyboard_comparison"],
        skip=True,
    ),
    TestCase(                                                                   # 37
        prompt=(
            "Search Amazon India for wireless earphones under 1500 rupees. "
            "Get the list with prices and ratings. "
            "Also search the web for 'best wireless earphones under 1500 review India 2024'. "
            "Save combined info to earphone_research.txt on the Desktop."
        ),
        group="ecommerce",
        note="ecommerce(amazon) + research(web review) + file",
        agents=["ecommerce_agent", "research_agent", "file_agent"],
        expect=["earphone", "earphone_research"],
        skip=True,
    ),
    TestCase(                                                                   # 38
        prompt=(
            "Find running shoes under 2500 rupees on Myntra. Show list with prices and ratings. "
            "Also check the weather in Nellore today to see if it is good for running. "
            "Save both to running_plan.txt on the Desktop."
        ),
        group="ecommerce",
        note="ecommerce(myntra) + research(weather) + file",
        agents=["ecommerce_agent", "research_agent", "file_agent"],
        expect=["shoe", "running_plan"],
        skip=True,
    ),

    # ══════════════════════════════════════════════════════════════════════════
    #  SYSTEM — needs Windows desktop
    # ══════════════════════════════════════════════════════════════════════════

    TestCase(                                                                   # 39
        prompt=(
            "Create a file on the Desktop called meeting_notes.txt with today's date "
            "as heading and the text 'Meeting notes for today'. Then open it in Notepad."
        ),
        group="system",
        note="utility(date) + file(create) + system(notepad)",
        agents=["utility_agent", "file_agent", "system_agent"],
        expect=["meeting_notes", "notepad"],
        skip=True,
    ),
    TestCase(                                                                   # 40
        prompt=(
            "Take a screenshot of the current screen, save it as screenshot_test.png "
            "on the Desktop, then open the Desktop folder in Windows Explorer."
        ),
        group="system",
        note="utility(screenshot) + file(save) + system(explorer)",
        agents=["utility_agent", "file_agent", "system_agent"],
        expect=["screenshot_test", "desktop"],
        skip=True,
    ),
    TestCase(                                                                   # 41
        prompt=(
            "Open Notepad, type 'Hello SentinelAI' into it, "
            "then save the file as test_notepad.txt on the Desktop"
        ),
        group="system",
        note="system(open + type) + file(save) — app interaction",
        agents=["system_agent", "file_agent"],
        skip=True,
    ),

    # ══════════════════════════════════════════════════════════════════════════
    #  COMMS — needs WhatsApp Web or Telegram open
    # ══════════════════════════════════════════════════════════════════════════

    TestCase(                                                                   # 42
        prompt=(
            "Search the web for today's top tech news headline. "
            "Save it to today_news.txt on the Desktop. "
            "Then send a WhatsApp message to Mom saying: "
            "Today's top tech news: [the headline you found]"
        ),
        group="comms",
        note="research + file + comms(whatsapp)",
        agents=["research_agent", "file_agent", "comms_agent"],
        expect=["today_news"],
        skip=True,
    ),
    TestCase(                                                                   # 43
        prompt=(
            "Get the current weather in Chennai. "
            "Send a Telegram message to my saved messages: Chennai weather update: [details]. "
            "Also save the weather to chennai_weather.txt on the Desktop."
        ),
        group="comms",
        note="research(weather) + comms(telegram) + file",
        agents=["research_agent", "comms_agent", "file_agent"],
        expect=["chennai", "chennai_weather"],
        skip=True,
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
#  COLORS
# ─────────────────────────────────────────────────────────────────────────────

G = "\033[92m"   # green
R = "\033[91m"   # red
Y = "\033[93m"   # yellow
C = "\033[96m"   # cyan
B = "\033[1m"    # bold
D = "\033[2m"    # dim
X = "\033[0m"    # reset


def _c(color: str, text: str):
    print(f"{color}{text}{X}")


# ─────────────────────────────────────────────────────────────────────────────
#  --run PARSER  ("3"  /  "1,4,7"  /  "2-6"  /  "1,3-5,9")
# ─────────────────────────────────────────────────────────────────────────────

def parse_run(value: str, total: int) -> list[int]:
    indices: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            indices.update(range(int(a), int(b) + 1))
        else:
            indices.add(int(part))
    valid = sorted(i for i in indices if 1 <= i <= total)
    if not valid:
        print(f"{R}No valid test numbers in '{value}' (valid: 1–{total}).{X}")
        sys.exit(1)
    return valid


# ─────────────────────────────────────────────────────────────────────────────
#  FILTER TESTS
# ─────────────────────────────────────────────────────────────────────────────

def filter_tests(
    args,
    numbered: list[tuple[int, TestCase]],
) -> list[tuple[int, TestCase]]:

    # --run overrides all other filters (and always includes skip=True)
    if args.run:
        indices = parse_run(args.run, len(ALL_TESTS))
        return [(n, t) for n, t in numbered if n in indices]

    result = numbered

    if args.group:
        result = [(n, t) for n, t in result if t.group == args.group]

    if args.agent:
        q = args.agent.lower()
        result = [(n, t) for n, t in result if any(q in a.lower() for a in t.agents)]

    if args.mixed:
        result = [(n, t) for n, t in result if len(t.agents) >= 2]

    if not args.all:
        result = [(n, t) for n, t in result if not t.skip]

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  LIST COMMAND
# ─────────────────────────────────────────────────────────────────────────────

def cmd_list(args):
    numbered  = list(enumerate(ALL_TESTS, 1))
    filtered  = filter_tests(args, numbered)

    if not filtered:
        print(f"\n  {Y}No tests match the current filters.{X}\n")
        return

    # Counts per group
    gc: dict[str, int] = {}
    for _, t in filtered:
        gc[t.group] = gc.get(t.group, 0) + 1
    group_info = "  ".join(f"{g}({c})" for g, c in sorted(gc.items()))

    print()
    print(f"  {B}SentinelAI v2 — Test List{X}  ({len(filtered)} tests)")
    print(f"  Groups: {C}{group_info}{X}")
    print()
    print(f"  {'#':>4}  {'Group':12}  {'Agents':38}  Note")
    print(f"  {'─'*4}  {'─'*12}  {'─'*38}  {'─'*28}")

    for num, t in filtered:
        agents_str = ", ".join(t.agents) if t.agents else "auto"
        if len(agents_str) > 37:
            agents_str = agents_str[:35] + ".."
        note_str  = t.note[:32] if t.note else ""
        skip_flag = f"  {Y}[browser/desktop]{X}" if t.skip else ""
        print(f"  {num:>4}  {t.group:12}  {C}{agents_str:38}{X}  {note_str}{skip_flag}")

    print()
    print(f"  {D}Run by number:    python test_prompts.py --run 5{X}")
    print(f"  {D}Run a range:      python test_prompts.py --run 17-29{X}")
    print(f"  {D}Run mixed list:   python test_prompts.py --run 3,7,17,24{X}")
    print(f"  {D}By group:         python test_prompts.py --group noui{X}")
    print(f"  {D}By agent:         python test_prompts.py --agent file_agent{X}")
    print(f"  {D}Multi-agent only: python test_prompts.py --mixed{X}")
    print(f"  {D}Include browser:  python test_prompts.py --all{X}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
#  BUILD ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def build_orch():
    from app.src.orchestrator import build_orchestrator

    def on_progress(msg: str):
        if any(k in msg for k in
               ["Submitting", "✓", "✗", "failed", "Processing", "Done in", "Building task"]):
            print(f"    {C}│{X} {msg}")

    return build_orchestrator(on_progress=on_progress)


# ─────────────────────────────────────────────────────────────────────────────
#  RUN ONE TEST
# ─────────────────────────────────────────────────────────────────────────────

def run_one(orch, num: int, test: TestCase, idx: int, total: int) -> bool:
    print()
    print(f"  {'─'*70}")
    print(f"  {B}[{idx}/{total}]{X}  #{num}  {C}{test.note}{X}")
    agents_str = ", ".join(test.agents) if test.agents else "auto"
    print(f"  Agents:  {agents_str}")

    # Wrap prompt for display
    words = test.prompt.split()
    line, display_lines = [], []
    for w in words:
        if sum(len(x) + 1 for x in line) + len(w) > 68:
            display_lines.append(" ".join(line))
            line = [w]
        else:
            line.append(w)
    if line:
        display_lines.append(" ".join(line))
    for i, dl in enumerate(display_lines):
        prefix = "  Prompt:  \"" if i == 0 else "            "
        print(f"{prefix}{dl}")
    print("  \"")
    if test.expect:
        print(f"  Expects:  {test.expect}")
    print()

    start = time.time()
    try:
        result  = orch.run(test.prompt)
        elapsed = time.time() - start
        answer  = result.get("answer", "").strip()
        runs    = result.get("task_results", [])
        n_ok    = sum(1 for r in runs if r.success)
        n_all   = len(runs)

        for r in runs:
            mark = f"{G}✓{X}" if r.success else f"{R}✗{X}"
            out  = (r.output or r.error or "")[:65]
            print(f"    {mark} {r.agent:22} {r.duration:5.1f}s  {D}{out}...{X}")
        if runs:
            print()

        missing = [kw for kw in test.expect if kw.lower() not in answer.lower()]
        if missing:
            _c(R, f"  ✗ FAIL  ({elapsed:.1f}s)  — missing in answer: {missing}")
            print(f"  Answer: {answer[:350]}")
            return False
        else:
            _c(G, f"  ✓ PASS  ({elapsed:.1f}s)  — {n_ok}/{n_all} agents OK")
            print(f"  Answer: {answer[:350]}{'...' if len(answer) > 350 else ''}")
            return True

    except Exception as exc:
        elapsed = time.time() - start
        _c(R, f"  ✗ EXCEPTION  ({elapsed:.1f}s)  — {type(exc).__name__}: {exc}")
        import traceback
        print(traceback.format_exc()[-600:])
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SentinelAI v2 — Agent Test Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python test_prompts.py --list                    see all tests numbered
  python test_prompts.py --list --group noui       see headless tests
  python test_prompts.py --list --mixed            see multi-agent tests
  python test_prompts.py --list --all              see every test including browser
  python test_prompts.py --run 5                   run test #5
  python test_prompts.py --run 1-5                 run tests #1 through #5
  python test_prompts.py --run 3,7,12              run tests #3, #7, #12
  python test_prompts.py --run 17-29               run all noui multi-agent tests
  python test_prompts.py --group noui              run all headless tests
  python test_prompts.py --group browser --all     run browser tests
  python test_prompts.py --group ecommerce --all   run ecommerce tests
  python test_prompts.py --agent file_agent        all tests that use file_agent
  python test_prompts.py --agent research_agent    all tests that use research_agent
  python test_prompts.py --mixed                   only multi-agent (2+) tests
  python test_prompts.py --mixed --group noui      multi-agent headless only
  python test_prompts.py --all                     every test (all groups)
  python test_prompts.py --dry-run                 print what would run
  python test_prompts.py --stop-on-fail            stop at first failure
  python test_prompts.py --prompt "open notepad"   run any custom prompt
        """,
    )

    parser.add_argument("--list",         action="store_true",
                        help="Show numbered test list (combine with other flags to filter)")
    parser.add_argument("--run",          metavar="N",
                        help="Run specific tests: --run 5  --run 1-5  --run 3,7,12")
    parser.add_argument("--group",        metavar="GROUP",
                        help="Filter by group: noui browser ecommerce system comms "
                             "research file utility memory edge")
    parser.add_argument("--agent",        metavar="AGENT",
                        help="Filter by agent: file_agent research_agent browser_agent "
                             "ecommerce_agent utility_agent system_agent comms_agent")
    parser.add_argument("--mixed",        action="store_true",
                        help="Only tests that involve 2+ agents")
    parser.add_argument("--all",          action="store_true",
                        help="Include browser/desktop tests (skip=True by default)")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Print what would run without executing agents")
    parser.add_argument("--stop-on-fail", action="store_true",
                        help="Stop after the first failing test")
    parser.add_argument("--prompt",       metavar="TEXT",
                        help="Run a single custom prompt (ignores all other filters)")

    args = parser.parse_args()
    numbered = list(enumerate(ALL_TESTS, 1))

    # ── --list ────────────────────────────────────────────────────────────────
    if args.list:
        cmd_list(args)
        return

    # ── --prompt (custom one-off) ─────────────────────────────────────────────
    if args.prompt:
        print(f"\n  {B}Custom Prompt{X}")
        print(f"  Prompt: \"{args.prompt}\"\n")
        orch = build_orch()
        custom = TestCase(prompt=args.prompt, group="custom", note="custom prompt")
        run_one(orch, 0, custom, 1, 1)
        return

    # ── Build test list ───────────────────────────────────────────────────────
    to_run = filter_tests(args, numbered)

    if not to_run:
        print(f"\n  {Y}No tests match your filters.{X}")
        print(f"  Try:  python test_prompts.py --list --all\n")
        sys.exit(1)

    # ── Dry run ───────────────────────────────────────────────────────────────
    if args.dry_run:
        print(f"\n  {B}Dry Run — {len(to_run)} test(s) would run:{X}\n")
        for num, t in to_run:
            skip_note = f"  {Y}[needs browser/desktop]{X}" if t.skip else ""
            agents_str = ", ".join(t.agents) if t.agents else "auto"
            print(f"  #{num:>3}  [{t.group:12}]  {C}{agents_str}{X}{skip_note}")
            print(f"        {t.note}")
            print(f"        \"{t.prompt[:80]}{'...' if len(t.prompt) > 80 else ''}\"")
            print()
        return

    # ── Header ────────────────────────────────────────────────────────────────
    flags = []
    if args.run:    flags.append(f"--run {args.run}")
    if args.group:  flags.append(f"--group {args.group}")
    if args.agent:  flags.append(f"--agent {args.agent}")
    if args.mixed:  flags.append("--mixed")
    if args.all:    flags.append("--all")
    flags_str = "  ".join(flags) or "default (headless only)"

    print()
    print("═" * 72)
    print(f"  {B}SentinelAI v2 — Prompt Test Suite{X}")
    print(f"  Running {len(to_run)} test(s)   Filters: {flags_str}")
    print("═" * 72)

    # ── Build orchestrator ────────────────────────────────────────────────────
    print(f"\n  Loading orchestrator (9 agents + memory)...")
    try:
        orch = build_orch()
    except Exception as e:
        _c(R, f"\n  FATAL: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
    _c(G, "  Orchestrator ready.\n")

    # ── Run ───────────────────────────────────────────────────────────────────
    passed, failed = [], []
    total = len(to_run)
    wall  = time.time()

    for run_idx, (num, test) in enumerate(to_run, 1):
        ok = run_one(orch, num, test, run_idx, total)
        (passed if ok else failed).append((num, test))
        if not ok and args.stop_on_fail:
            _c(Y, "\n  --stop-on-fail triggered.")
            break

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed_total = time.time() - wall
    print()
    print("═" * 72)
    print(
        f"  {B}RESULTS{X}  "
        f"{G}{len(passed)} passed{X}  /  "
        f"{R}{len(failed)} failed{X}  /  "
        f"{total} total  —  {elapsed_total:.1f}s"
    )
    if failed:
        print()
        _c(R, "  FAILED:")
        for num, t in failed:
            print(f"    ✗  #{num}  [{t.group}]  {t.note}")
    else:
        _c(G, "  ALL PASSED ✓")
    print("═" * 72)
    print()
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()




