"""
research_agent.py — Redesigned Research Agent for SentinelAI v2

WHAT THIS AGENT DOES:
  Pure API/HTTP research — no browser needed, no CDP, no Playwright.
  Fast, reliable, works offline-friendly, never opens new windows.

TOOLS:
  web_search         — DuckDuckGo text search (always works, no key)
  tavily_search      — Tavily AI search (higher quality, needs TAVILY_API_KEY)
  deep_web_search    — Multi-engine: runs DuckDuckGo + Tavily in parallel, deduplicates
  fetch_page_text    — Fetch and extract clean text from any URL via HTTP (no browser)
  youtube_search     — YouTube video search via DuckDuckGo video index
  wikipedia_search   — Wikipedia article summary + URL
  arxiv_search       — ArXiv research papers
  weather_search     — Current weather + 3-day forecast via wttr.in (no key)
  news_search        — Recent news articles via DuckDuckGo news index
  answer_question    — Direct factual Q&A: synthesizes across all available sources
  summarize_url      — Fetch a URL and return a structured summary
  extract_links      — Pull all URLs from a block of text
  search_and_summarize — Search + fetch top result + return clean summary in one call

DESIGN RULES:
  - Every tool takes exactly 1-2 arguments (no optional kwargs) — prevents LLM arg errors
  - All network calls have timeouts — never hangs
  - Graceful fallback: if one source fails, tries the next
  - No hallucination: if nothing found, says so explicitly
  - Parallel execution where it speeds things up

INSTALL:
  pip install duckduckgo-search tavily-python wikipedia arxiv
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import urllib.request
import urllib.parse
from typing import Optional

from langchain_core.tools import tool
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder


class _ToolOutputLogger(BaseCallbackHandler):
    """Prints tool inputs and outputs so they appear in logs."""

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = serialized.get("name", "tool")
        display = str(input_str)
        display = display if len(display) < 400 else display[:400] + "..."
        print(f"[Tool→{name}] INPUT: {display}")

    def on_tool_end(self, output, **kwargs):
        out = str(output)
        display = out if len(out) < 600 else out[:600] + "...[truncated]"
        print(f"[Tool OUTPUT]: {display}")

    def on_tool_error(self, error, **kwargs):
        print(f"[Tool ERROR]: {error}")


_TOOL_LOGGER = _ToolOutputLogger()


# =============================================================================
#  INTERNAL HELPERS
# =============================================================================

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _ddg_text(query: str, max_results: int = 10) -> list[dict]:
    """DuckDuckGo text search. Returns list of {title, href, body}."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []


def _ddg_news(query: str, max_results: int = 8) -> list[dict]:
    """DuckDuckGo news search. Returns list of {title, url, body, date, source}."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.news(query, max_results=max_results))
    except Exception:
        return []


def _tavily_search(query: str, max_results: int = 8) -> list[dict]:
    """Tavily AI search. Returns list of {title, url, content}."""
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=max_results,
            include_answer=True,
        )
        results = []
        if response.get("answer"):
            results.append({
                "title": "Tavily Direct Answer",
                "url": "",
                "content": response["answer"],
                "is_answer": True,
            })
        for r in response.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:400],
                "is_answer": False,
            })
        return results
    except Exception:
        return []


def _fetch_url_text(url: str, max_chars: int = 6000) -> str:
    """Fetch a URL and return clean extracted text. No browser needed."""
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
            encoding = resp.headers.get_content_charset("utf-8") or "utf-8"
            html = raw.decode(encoding, errors="replace")

        # Strip scripts, styles, navigation
        html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<nav[^>]*>.*?</nav>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<header[^>]*>.*?</header>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<footer[^>]*>.*?</footer>", " ", html, flags=re.DOTALL | re.IGNORECASE)

        # Try trafilatura for best text extraction
        try:
            import trafilatura  # type: ignore
            text = trafilatura.extract(html, include_links=False, include_images=False)
            if text and len(text) > 200:
                return text[:max_chars]
        except ImportError:
            pass

        # Fallback: strip all HTML tags
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&[a-z]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        return f"[Fetch failed for {url}: {e}]"


def _format_ddg_results(results: list[dict], max_body: int = 250) -> str:
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("href", r.get("url", ""))
        body = (r.get("body", r.get("content", "")) or "")[:max_body]
        lines.append(f"{i}. {title}\n   URL: {url}\n   {body}")
    return "\n\n".join(lines)


# =============================================================================
#  TOOLS
# =============================================================================

@tool
def web_search(query: str) -> str:
    """Search the web using DuckDuckGo. Returns top 10 results with titles, URLs, snippets.

    Best for: general questions, current events, reviews, how-to guides, product info.
    No API key needed. Always works.

    Args:
        query: What to search for. Be specific for better results.
               Examples: "best laptop under 50000 rupees 2024"
                         "how to fix Python ImportError"
                         "Nellore weather today"
    """
    results = _ddg_text(query, max_results=10)
    return _format_ddg_results(results)


@tool
def tavily_search(query: str) -> str:
    """Search using Tavily AI — high-quality results with a direct answer.

    Returns a synthesized answer + cited sources. Much better than web_search
    for factual questions, recent news, and research tasks.
    Falls back to DuckDuckGo if TAVILY_API_KEY is not set.

    Args:
        query: What to search for.
               Examples: "latest AI models released 2024"
                         "Python asyncio best practices"
                         "India GDP growth rate 2024"
    """
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        # Transparent fallback
        results = _ddg_text(query, max_results=10)
        return "[Tavily not configured — using DuckDuckGo]\n\n" + _format_ddg_results(results)

    results = _tavily_search(query, max_results=8)
    if not results:
        return web_search.invoke({"query": query})

    lines = []
    for r in results:
        if r.get("is_answer"):
            lines.append(f"DIRECT ANSWER: {r['content']}\n")
        else:
            lines.append(f"• {r['title']}\n  URL: {r['url']}\n  {r['content']}")
    return "\n\n".join(lines)


@tool
def deep_web_search(query: str) -> str:
    """Run DuckDuckGo AND Tavily in parallel, merge and deduplicate results.

    Use for important research tasks where you want the best possible coverage.
    Returns up to 15 unique results from both engines combined.

    Args:
        query: Your research question or topic.
               Examples: "machine learning engineer salary India 2024"
                         "React vs Vue vs Angular 2024 comparison"
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        ddg_future = ex.submit(_ddg_text, query, 10)
        tav_future = ex.submit(_tavily_search, query, 8)
        ddg_results = ddg_future.result()
        tav_results = tav_future.result()

    # Merge and deduplicate by URL
    seen_urls: set[str] = set()
    merged = []

    # Tavily direct answer first
    for r in tav_results:
        if r.get("is_answer"):
            merged.append(f"TAVILY ANSWER: {r['content']}\n")

    # DDG results
    for r in ddg_results:
        url = r.get("href", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(
                f"• {r.get('title', '')}\n"
                f"  URL: {url}\n"
                f"  {(r.get('body', '') or '')[:200]}"
            )

    # Tavily sources (skip duplicates)
    for r in tav_results:
        if r.get("is_answer"):
            continue
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(
                f"• {r.get('title', '')}\n"
                f"  URL: {url}\n"
                f"  {(r.get('content', '') or '')[:200]}"
            )

    if not merged:
        return "No results found from any search engine."

    return "\n\n".join(merged[:15])


@tool
def fetch_page_text(url: str) -> str:
    """Fetch a webpage and return its clean text content. No browser needed — pure HTTP.

    Use when you have a specific URL and want to read its full content:
    articles, blog posts, documentation, Wikipedia pages, news articles.

    Returns up to 6000 characters of clean text (scripts/ads/nav removed).

    Args:
        url: Full URL starting with http:// or https://
             Example: "https://en.wikipedia.org/wiki/Machine_learning"
             Example: "https://docs.python.org/3/library/asyncio.html"
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    text = _fetch_url_text(url, max_chars=6000)
    if text.startswith("[Fetch failed"):
        return text
    return f"Content from {url}:\n\n{text}"


@tool
def search_and_summarize(query: str) -> str:
    """Search the web, fetch the top result, and return a clean structured summary.

    This is the power tool for research: one call gets you a search + full article
    content in one shot. Best for questions that need a definitive answer.

    Flow: DuckDuckGo search → fetch top URL → extract clean text → return

    Args:
        query: Your research question.
               Examples: "what is RAG in AI"
                         "how does Python GIL work"
                         "best practices for FastAPI production deployment"
    """
    results = _ddg_text(query, max_results=5)
    if not results:
        return f"No search results for: {query}"

    top = results[0]
    url = top.get("href", "")
    title = top.get("title", "")
    snippet = (top.get("body", "") or "")[:300]

    summary_lines = [
        f"QUERY: {query}",
        f"TOP RESULT: {title}",
        f"URL: {url}",
        f"SNIPPET: {snippet}",
    ]

    # Fetch full page
    if url:
        full_text = _fetch_url_text(url, max_chars=4000)
        if not full_text.startswith("[Fetch failed"):
            summary_lines.append(f"\nFULL CONTENT:\n{full_text}")
        else:
            # Fallback: return snippets from all 5 results
            summary_lines.append("\nOTHER RESULTS:")
            for r in results[1:5]:
                summary_lines.append(
                    f"• {r.get('title', '')}: {(r.get('body', '') or '')[:200]}"
                    f"\n  {r.get('href', '')}"
                )

    return "\n".join(summary_lines)


@tool
def news_search(query: str) -> str:
    """Search for recent news articles on a topic. Returns articles from the past week.

    Best for: breaking news, recent events, latest releases, company announcements,
    market news, sports results, political developments.

    Args:
        query: News topic to search.
               Examples: "OpenAI GPT-5 release"
                         "India budget 2024 highlights"
                         "IPL 2024 results today"
    """
    results = _ddg_news(query, max_results=10)
    if not results:
        # Fallback to regular search with "news" suffix
        ddg_results = _ddg_text(f"{query} news latest", max_results=8)
        return "[News index empty — web search fallback]\n\n" + _format_ddg_results(ddg_results)

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", r.get("href", ""))
        body = (r.get("body", "") or "")[:200]
        source = r.get("source", "")
        date = r.get("date", "")
        line = f"{i}. {title}"
        if source:
            line += f"  ({source}"
            if date:
                line += f", {date}"
            line += ")"
        line += f"\n   URL: {url}"
        if body:
            line += f"\n   {body}"
        lines.append(line)

    return f"NEWS results for '{query}':\n\n" + "\n\n".join(lines)


@tool
def youtube_search(query: str) -> str:
    """Search YouTube for videos. Returns titles, watch URLs, channel names, views.

    Best for: tutorials, talks, demos, music, vlogs, reviews, any video content.
    No API key needed.

    Args:
        query: What to search for on YouTube.
               Examples: "Python FastAPI tutorial 2024"
                         "machine learning explained simply"
                         "Carnatic music Nellore"
    """
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.videos(query, max_results=8))
        if results:
            lines = []
            for i, r in enumerate(results, 1):
                title = r.get("title", "")
                url = r.get("content", r.get("url", ""))
                channel = r.get("uploader", r.get("publisher", ""))
                views = r.get("statistics", {}).get("viewCount", "") if isinstance(r.get("statistics"), dict) else ""
                line = f"{i}. {title}"
                if channel:
                    line += f"  — {channel}"
                if views:
                    try:
                        line += f"  ({int(views):,} views)"
                    except ValueError:
                        pass
                line += f"\n   URL: {url}"
                lines.append(line)
            return f"YouTube results for '{query}':\n\n" + "\n\n".join(lines)
    except Exception:
        pass

    # Fallback: web search limited to youtube.com
    results = _ddg_text(f"site:youtube.com {query}", max_results=8)
    return _format_ddg_results(results)


@tool
def wikipedia_search(query: str) -> str:
    """Search Wikipedia and return a detailed article summary with the URL.

    Best for: definitions, history, science, people, places, concepts, terminology.
    Returns 8 sentences of the article summary + URL.

    Args:
        query: Topic to look up on Wikipedia.
               Examples: "transformer neural network"
                         "Nellore city history"
                         "Python programming language"
    """
    try:
        import wikipedia
        wikipedia.set_lang("en")
        try:
            summary = wikipedia.summary(query, sentences=8, auto_suggest=False)
            page = wikipedia.page(query, auto_suggest=False)
            return f"Wikipedia: {page.title}\nURL: {page.url}\n\n{summary}"
        except wikipedia.exceptions.DisambiguationError as e:
            # Take the first meaningful option
            best = next((o for o in e.options if "may refer" not in o.lower()), e.options[0])
            summary = wikipedia.summary(best, sentences=8, auto_suggest=False)
            return f"Wikipedia ({best}):\n\n{summary}"
        except wikipedia.exceptions.PageError:
            # Suggest close options
            suggestions = wikipedia.search(query, results=5)
            if suggestions:
                summary = wikipedia.summary(suggestions[0], sentences=5, auto_suggest=False)
                return f"No exact match for '{query}'. Closest: {suggestions[0]}\n\n{summary}"
            return f"No Wikipedia article found for '{query}'."
    except ImportError:
        # Fallback: fetch Wikipedia directly
        encoded = urllib.parse.quote(query.replace(" ", "_"))
        text = _fetch_url_text(f"https://en.wikipedia.org/wiki/{encoded}", max_chars=3000)
        if not text.startswith("[Fetch failed"):
            return f"Wikipedia ({query}):\n{text}"
        return web_search.invoke({"query": f"wikipedia {query}"})
    except Exception as e:
        return f"Wikipedia search failed: {e}"


@tool
def arxiv_search(query: str) -> str:
    """Search ArXiv for research papers. Returns title, authors, abstract, PDF link.

    Best for: AI/ML papers, physics, math, computer science, academic research,
    finding the latest methods and benchmarks.

    Args:
        query: Research topic or paper title keywords.
               Examples: "attention is all you need transformer"
                         "retrieval augmented generation survey"
                         "reinforcement learning from human feedback"
    """
    try:
        import arxiv
        client = arxiv.Client()
        search = arxiv.Search(
            query=query,
            max_results=5,
            sort_by=arxiv.SortCriterion.Relevance,
        )
        results = []
        for r in client.results(search):
            authors = ", ".join(a.name for a in r.authors[:4])
            if len(r.authors) > 4:
                authors += " et al."
            results.append(
                f"{len(results)+1}. {r.title}\n"
                f"   Authors: {authors}\n"
                f"   Published: {r.published.strftime('%Y-%m-%d')}\n"
                f"   ArXiv: {r.entry_id}\n"
                f"   PDF: {r.pdf_url}\n"
                f"   Abstract: {r.summary[:400]}..."
            )
        return "\n\n".join(results) if results else f"No ArXiv papers found for: {query}"
    except ImportError:
        return web_search.invoke({"query": f"site:arxiv.org {query}"})
    except Exception as e:
        return f"ArXiv search failed: {e}"


@tool
def weather_search(location: str) -> str:
    """Get current weather conditions and 3-day forecast for any city worldwide.

    No API key needed. Works for any city name.

    Args:
        location: City or place name.
                  Examples: "Nellore", "Hyderabad", "Mumbai", "London", "New York"
    """
    try:
        encoded = urllib.parse.quote(location)
        url = f"https://wttr.in/{encoded}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        cur = data["current_condition"][0]
        desc  = cur["weatherDesc"][0]["value"]
        temp  = cur["temp_C"]
        feels = cur["FeelsLikeC"]
        humid = cur["humidity"]
        wind  = cur["windspeedKmph"]
        wdir  = cur["winddir16Point"]
        vis   = cur.get("visibility", "?")
        uv    = cur.get("uvIndex", "?")

        forecast_lines = []
        for day in data.get("weather", [])[:3]:
            day_desc = day["hourly"][4]["weatherDesc"][0]["value"]
            sunrise  = day.get("astronomy", [{}])[0].get("sunrise", "")
            sunset   = day.get("astronomy", [{}])[0].get("sunset", "")
            forecast_lines.append(
                f"  {day['date']}: {day['mintempC']}°C – {day['maxtempC']}°C  {day_desc}"
                + (f"  | Sunrise {sunrise} Sunset {sunset}" if sunrise else "")
            )

        return (
            f"Weather in {location.title()}:\n"
            f"  Condition  : {desc}\n"
            f"  Temperature: {temp}°C (feels like {feels}°C)\n"
            f"  Humidity   : {humid}%\n"
            f"  Wind       : {wind} km/h {wdir}\n"
            f"  Visibility : {vis} km\n"
            f"  UV Index   : {uv}\n\n"
            f"3-Day Forecast:\n" + "\n".join(forecast_lines)
        )
    except Exception as e:
        # Plain-text fallback
        try:
            url2 = f"https://wttr.in/{urllib.parse.quote(location)}?format=3"
            req2 = urllib.request.Request(url2, headers={"User-Agent": "curl/7.0"})
            with urllib.request.urlopen(req2, timeout=8) as resp:
                return resp.read().decode("utf-8").strip()
        except Exception:
            return f"Weather lookup failed for '{location}': {e}"


@tool
def answer_question(question: str) -> str:
    """Answer a factual question by searching multiple sources and synthesizing the answer.

    Uses DuckDuckGo + Tavily in parallel, then picks the best answer.
    Best for: direct factual questions where you need a confident answer fast.

    Args:
        question: A specific factual question.
                  Examples: "What is the capital of Andhra Pradesh?"
                            "When was Python first released?"
                            "What LPA does a senior ML engineer earn in Hyderabad?"
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        ddg_future = ex.submit(_ddg_text, question, 5)
        tav_future = ex.submit(_tavily_search, question, 5)
        ddg_results = ddg_future.result()
        tav_results = tav_future.result()

    answer_lines = [f"Q: {question}\n"]

    # Tavily direct answer is best
    for r in tav_results:
        if r.get("is_answer"):
            answer_lines.append(f"ANSWER: {r['content']}\n")
            break

    # Supporting sources
    answer_lines.append("SOURCES:")
    for r in (tav_results + ddg_results)[:6]:
        if r.get("is_answer"):
            continue
        title = r.get("title", "")
        url = r.get("url", r.get("href", ""))
        body = (r.get("content", r.get("body", "")) or "")[:200]
        answer_lines.append(f"• {title}: {body}\n  {url}")

    return "\n".join(answer_lines)


@tool
def summarize_url(url: str) -> str:
    """Fetch a URL and return a clean structured summary of its content.

    Automatically extracts the main text (removes ads, nav, scripts).
    Best for: reading articles, docs, blog posts, news pages in full.

    Args:
        url: Full URL to fetch and summarize.
             Example: "https://arxiv.org/abs/2307.09288"
             Example: "https://blog.langchain.dev/rag-from-scratch"
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    text = _fetch_url_text(url, max_chars=6000)
    if text.startswith("[Fetch failed"):
        return f"Could not fetch {url}: {text}"

    # Extract title-like first line if available
    first_line = text[:200].split("\n")[0].strip()
    return f"URL: {url}\n\nSUMMARY:\n{text}"


@tool
def extract_links(text: str) -> str:
    """Extract all URLs from a block of text. Returns one URL per line, deduplicated.

    Use for: pulling links out of search results, page content, or agent output.

    Args:
        text: Any text that may contain URLs.
    """
    urls = re.findall(r"https?://[^\s\)\"\'\<\>]+", text)
    seen: set[str] = set()
    unique = []
    for u in urls:
        # Strip trailing punctuation
        u = u.rstrip(".,;:!?)")
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return "\n".join(unique) if unique else "No URLs found in text."


# =============================================================================
#  AGENT BUILDER
# =============================================================================

def build_research_agent(llm) -> AgentExecutor:
    """Build the ResearchAgent — fast, multi-source, API-only research."""

    tools = [
        tavily_search,
        web_search,
        deep_web_search,
        fetch_page_text,
        search_and_summarize,
        news_search,
        youtube_search,
        wikipedia_search,
        arxiv_search,
        weather_search,
        answer_question,
        summarize_url,
        extract_links,
    ]

    tavily_ok = bool(os.getenv("TAVILY_API_KEY", "").strip())
    tavily_status = "ACTIVE" if tavily_ok else "NOT CONFIGURED (falls back to DuckDuckGo)"

    system_prompt = f"""You are ResearchAgent — the knowledge and search specialist for SentinelAI.

You are FAST, ACCURATE, and HONEST. You search the web via HTTP APIs — no browser needed, no CDP.
You never make up facts. If you can't find something, say so clearly.

Tavily AI search: {tavily_status}

════════════════════════════════════════════════════════════
TOOLS — WHAT EACH ONE IS FOR
════════════════════════════════════════════════════════════

web_search(query)
  DuckDuckGo text search. 10 results with titles, URLs, snippets.
  Use for: fallback only — if tavily_search fails or returns nothing.

tavily_search(query)
  Tavily AI search. Best quality — gives a synthesized ANSWER + sources.
  Use for: factual questions, current events, detailed research, product info.
  DEFAULT choice for web searches. Use web_search only as fallback.

deep_web_search(query)
  Runs DDG + Tavily in parallel, merges results. 15 unique sources.
  Use for: important research where you want maximum coverage.

fetch_page_text(url)
  Fetch any URL and read its full clean text. NO browser needed.
  Use when you have a specific URL and want to read the full article.

search_and_summarize(query)
  Search + fetch top result + return clean summary in ONE call.
  Use for: research questions where you want a definitive answer with content.

news_search(query)
  Recent news articles from the past week via DuckDuckGo news index.
  Use for: breaking news, recent events, releases, announcements.

youtube_search(query)
  YouTube videos via DuckDuckGo video index. Titles, URLs, channels.
  Use for: tutorials, talks, music, demos, any YouTube content.

wikipedia_search(query)
  Wikipedia article summary (8 sentences) + URL.
  Use for: definitions, history, science, people, places, concepts.

arxiv_search(query)
  ArXiv research papers. Title, authors, abstract, PDF link.
  Use for: AI/ML papers, academic research, finding methods and benchmarks.

weather_search(location)
  Current weather + 3-day forecast via wttr.in. No API key needed.
  Use for: any weather question. Supports any city name worldwide.

answer_question(question)
  Parallel DDG + Tavily search, synthesized into a direct answer.
  Use for: quick factual Q&A where you want a confident, sourced answer fast.

summarize_url(url)
  Fetch a URL and return structured clean summary.
  Use when given a specific article/doc URL to read and summarize.

extract_links(text)
  Pull all URLs from a block of text.
  Use for: extracting URLs from search results or other tool output.

════════════════════════════════════════════════════════════
TOOL SELECTION GUIDE
════════════════════════════════════════════════════════════

"What is X?" / "Explain X"
  → wikipedia_search(X) first. Then tavily_search if more detail needed.

"Latest news about X" / "What happened with X recently?"
  → news_search(X)

"Find research papers on X" / "Latest ML paper on X"
  → arxiv_search(X)

"YouTube videos about X" / "Tutorial for X"
  → youtube_search(X)

"Weather in X" / "Is it raining in X?"
  → weather_search(X)

"What is the answer to X?" (factual question)
  → answer_question(X) — fastest path to a sourced answer

"Research X thoroughly" / "Compare X and Y"
  → deep_web_search(X) for broad coverage

"Read this article: <URL>" / "Summarize this page: <URL>"
  → summarize_url(URL) or fetch_page_text(URL)

"Search for X and give me the full article"
  → search_and_summarize(X)

════════════════════════════════════════════════════════════
QUALITY RULES
════════════════════════════════════════════════════════════

1. ALWAYS cite your sources. Every fact gets a URL.
2. NEVER fabricate URLs, papers, or statistics.
3. If search returns nothing: say "No results found for X" — don't guess.
4. For multiple sub-questions: use multiple tool calls.
5. Structure output clearly: use headers, bullet points, numbered lists.
6. For comparisons: use a table format where possible.
7. Keep responses concise — max 3 paragraphs unless detail is requested.

════════════════════════════════════════════════════════════
OUTPUT FORMAT
════════════════════════════════════════════════════════════

For factual answers:
  ANSWER: [direct answer in 1-2 sentences]
  SOURCE: [URL]
  DETAIL: [2-3 sentences of supporting context]

For research summaries:
  ## Topic
  [3-4 key points with citations]
  Sources: [numbered URL list]

For news:
  1. [Title] — [Source], [Date]
     [2-sentence summary]
     URL: [link]

For comparisons:
  | Feature | Option A | Option B |
  |---------|----------|----------|
  | ...     | ...      | ...      |"""

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
        max_iterations=10,
        callbacks=[_TOOL_LOGGER],
    )