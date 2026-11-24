"""Optional layer on top: a general LLM digests a request + documents, Jev does the clicking.

The planner runs once up front and once per verification, never per click, so the loop stays fast.
Any OpenAI-compatible endpoint works: Gemini (PLANNER_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai)
or local Ollama (http://127.0.0.1:11434/v1).
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

from .agent import Agent
from .contracts import (
    GAVE_UP,
    content_missing,
    evidence_holds,
    field_values,
    missing_goal_values,
    page_key,
    requirement_check,
    subject_missing,
)
from .demo import load_environment
from .model import MissingValue, json_content, post_json
from .questions import MAX_EARLY_CHECKS
from .recovery import MAX_RECOVERIES, RECOVERABLE, plan_recovery

PLAN = """You prepare a task for a fast browser agent that can click, type, select, scroll, and attach the user's listed
files to upload fields, but cannot read documents.
From the user's request and context, dissect the intent and automatically determine the starting URL, the exact goal,
and any profile facts.
Return JSON:
{"url": starting URL, "goal": one paragraph, "profile": {fact name: value}}
- url: automatically determine the best starting URL for the task:
  * If the user gave an explicit URL or the current open page matches the task, use it.
  * If the task is about a specific city, service, portal, or website (e.g. Leipzig residence permit, Wikipedia,
    airline flights, weather), determine the official portal URL (e.g. 'https://www.leipzig.de',
    'https://en.wikipedia.org', 'https://www.google.com/travel/flights').
  * If you are unsure of the exact deep link, provide an official search query like
    'https://html.duckduckgo.com/html/?q=...' so the agent searches and navigates there directly.
    Never leave url empty or invented.
  * Never start on a general web search engine's results page (google.com/search, bing.com/search): they show
    bot checks to automated browsers. Prefer the site that holds the answer (a shop's or portal's own search, a
    price-comparison site of the user's country for buying hardware, the official portal for procedures), or
    'https://html.duckduckgo.com/html/?q=...'.
- goal: begin with the user's actual question or request in their words, then the route. A question ("how can I
  apply", "which is the best/cheapest") is answered by reading pages, so the goal is to open and read the pages that
  hold the answer (requirements, steps, deadlines, prices, specs), and compare several options when the user asks
  for the best or cheapest, not to reach one page and stop. Directories and aggregators (programme databases,
  comparison sites) lead to the source: for how-to-apply or how-to-order questions, follow the link to the
  provider's own page (the university's application page, the shop's product page) for steps and deadlines.
  State every requirement explicitly (fields to fill, choices to make, where to stop). Include concrete values.
  Say to stop before any final submission or payment unless the user explicitly asked to submit or pay.
- profile: flat facts from the documents the agent may need to type (names, dates, numbers, addresses, emails).
  Copy values exactly. Never invent personal information; omit facts you do not have."""

VERIFY = """You check whether a browser agent finished its goal. Given the goal and the current page, return JSON:
{"requirements": [{"need": "one requirement from the goal", "met": true/false,
                   "quote": "short exact quote from the page that proves it, or empty"}],
 "done": true/false, "missing": "what is still not satisfied, or empty"}.
List EVERY requirement separately: each item, value, direction and unit (from AND to currencies, route start AND
end, dates, counts) and the kind of page asked for (search results vs an AI-answers, topic, keyword or home page;
an opened item vs a list). A value with the wrong unit, direction or page kind is not met. Do not list prohibitions
("do not sign in", "read-only") as requirements; only things that should be visible.
Judge only visible evidence. A homepage, a category or topic listing, a search form, a login page, an error page or
an unchanged input form is not completion. Every requirement in the goal (the specific item, the computed value,
the opened result) must be visible as the main subject of the page (its heading or main content), not only in a
link, a recent-searches chip or a sidebar. Quote evidence verbatim; if you cannot quote it, done is false.
Sites show dates in their own format, often without the year ("Fri, Jan 15" for 2027-01-15): that meets a date
requirement when day and month match. A search is done only when result items are visible, not just the query in
the search box or the title.
Requirements are outcomes, never process steps: "search for X" is met by X's own page or by results for X, not by
proof that a search box was used. A narrower place is not the place asked for: when the page's heading names a
district or suburb first ("Westminster, London, England"), a requirement for the city ("London") is NOT met;
and a tag, topic or category page is not search results.
Only the values the goal asks for count: never add requirements from the page itself (its filters, price ranges,
sort order, ads). field_values lists typed values and ticked checkboxes or chosen radios ("Label: checked").
A field the goal does not mention may hold a value the site filled in
itself (today's date, a default): ignore it. When the goal says not to submit, send or confirm, an unsent form is
exactly what was asked, never a missing step."""


ANSWER = """You report back to the user after a browser agent worked on their request. You get the request and the
pages the agent saw (URL, title, text). Answer the request directly from those pages, in the user's language, in a
few short paragraphs or a short list: the concrete findings (names, prices, links, steps, deadlines) with the URL each
came from. Use only what the pages show; never invent facts. Claims about specs (memory sizes, models), delivery,
shipping, stock, deadlines or eligibility need the page text to say so; otherwise say they were not checked.
Keep the user's constraints as the user meant them: a price cap is a hard maximum, but a spec that does not
exist in practice (a size no product comes in) means the closest realistic match. Recommend the best matches
first and say in one line how they differ from what was asked.
Cite only URLs that appear in the pages list or in their text.
If the pages do not answer it, say plainly what was found, what is missing and why (for example a bot check or a
login wall), and the best next step.
Return JSON: {"answer": "text for the user"}."""

# Pages kept for the final answer, and how much of each page's text.
NOTE_PAGES = 12
NOTE_CHARS = 3000


def note_page(notes, page):
    """Remember what the agent saw, newest last, so the final answer can cite every page, not only the last one."""
    url = page.get("url")
    text = str(page.get("text") or "").strip()
    if not url or not text:
        return
    notes.pop(url, None)
    notes[url] = {"url": url, "title": page.get("title"), "text": text[:NOTE_CHARS]}
    while len(notes) > NOTE_PAGES:
        notes.pop(next(iter(notes)))


def write_answer(request, notes, outcome):
    """The user asked a question, not for a verdict: turn what the agent saw into an answer."""
    outcome = {k: (v[:500] if isinstance(v, str) else v) for k, v in (outcome or {}).items()}
    pages = list(notes.values())
    reply = None
    # A provider may refuse a large request; try again with the newest few pages before giving up.
    for kept in (pages, pages[-4:]):
        try:
            reply = ask(ANSWER, json.dumps({"request": request, "outcome": outcome, "pages": kept}))
            break
        except Exception as exc:
            error = exc
    if reply is None:
        return f"I could not write up the result ({type(error).__name__}: {error})."
    return str((reply or {}).get("answer") or "").strip() if isinstance(reply, dict) else str(reply or "").strip()


_WALLED_SEARCH = re.compile(r"^https?://(www\.)?(google|bing)\.[a-z.]+/search\b", re.I)


def avoid_walled_search(url):
    """Search-engine result pages bot-wall automated browsers at once (google.com/sorry); keep the query, swap the
    engine for DuckDuckGo's HTML page, which stays usable."""
    if not _WALLED_SEARCH.match(url or ""):
        return url
    query = parse_qs(urlparse(url).query).get("q", [""])[0]
    return "https://html.duckduckgo.com/html/?q=" + quote_plus(query)


def verify(goal, page):
    """Ask the verifier, then accept "done" only if its quoted evidence is really on the page."""
    fields = field_values(page)
    content = json.dumps(
        {"goal": goal, "url": page["url"], "title": page.get("title"), "page": page["text"], "field_values": fields}
    )
    try:
        check = requirement_check(ask(VERIFY, content))
    except Exception as exc:
        # One provider blip must not end the run: an unverified page is simply "not confirmed yet".
        return {"done": False, "missing": f"Verifier unavailable ({type(exc).__name__}: {exc})", "evidence": []}
    proof = check.get("evidence")
    if check.get("done") and not evidence_holds(proof, page.get("text"), page.get("title"), page["url"], fields):
        check = {**check, "done": False, "missing": check.get("missing") or "Verifier evidence is not on the page."}
    absent = missing_goal_values(goal, page) if check.get("done") else []
    if check.get("done") and content_missing(page):
        check = {**check, "done": False, "missing": "The page's main content has not loaded yet (only a header)."}
    elif absent:
        check = {**check, "done": False, "missing": f"Not shown on the page: {', '.join(absent)}"}
    elif check.get("done") and subject_missing(goal, page):
        check = {**check, "done": False, "missing": "The page's title/URL is not about the goal's subject."}
    return check


def ask(system, content):
    base = (
        os.environ.get("PLANNER_BASE_URL") or os.environ.get("TEXT_MODEL_BASE_URL", "http://127.0.0.1:20128")
    ).rstrip("/")
    key = os.environ.get("PLANNER_API_KEY") or os.environ.get("TEXT_MODEL_API_KEY", "local")
    model = os.environ.get("PLANNER_MODEL") or os.environ.get("TEXT_MODEL", "claude-odysseus")
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        },
    )
    return json_content(result)


def documents(paths):
    """Text files and PDF text inline; images as data URLs (multimodal planners read them directly)."""
    parts = []
    for path in map(Path, paths):
        kind = mimetypes.guess_type(path.name)[0] or ""
        if kind.startswith("image/"):
            data = base64.b64encode(path.read_bytes()).decode()
            parts += [
                {"type": "text", "text": f"Document {path.name}:"},
                {"type": "image_url", "image_url": {"url": f"data:{kind};base64,{data}"}},
            ]
        elif path.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(path)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if text.strip():
                parts.append({"type": "text", "text": f"Document {path.name} (PDF):\n{text[:40000]}"})
            else:
                # Scanned PDF: extract embedded page images so multimodal model can read the passport/ID
                found = False
                for p_idx, page in enumerate(reader.pages[:3]):
                    for img in page.images:
                        b64 = base64.b64encode(img.data).decode()
                        mime = "image/jpeg" if "jp" in img.name.lower() else "image/png"
                        parts += [
                            {"type": "text", "text": f"Document {path.name} (page {p_idx + 1} scan):"},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        ]
                        found = True
                if not found:
                    parts.append({"type": "text", "text": f"Document {path.name}: (empty scanned PDF)"})
        else:
            parts.append({"type": "text", "text": f"Document {path.name}:\n{path.read_text(encoding='utf-8')}"})
    return parts


def run(request, docs=(), *, url=None, files=(), ask_user=input, report=print, rounds=3):
    ensure_local_server()
    context_lines = []
    if url:
        context_lines.append(f"Current open page in browser: {url}")
    if files:
        context_lines.append(f"Files the agent can attach to upload fields: {[Path(f).name for f in files]}")
    note = ("\n" + "\n".join(context_lines)) if context_lines else ""
    # The planner once wrote 2024-01-15 for "2027-01-15"-style relative or partial dates; give it today's date.
    note += f"\nToday's date: {time.strftime('%Y-%m-%d')}"
    plan = ask(PLAN, [{"type": "text", "text": request + note}, *documents(docs)])
    profile, goal = dict(plan.get("profile") or {}), plan["goal"]
    start_url = avoid_walled_search(plan.get("url") or url or "https://html.duckduckgo.com/html/")
    report(f"PLAN {start_url}\n  goal: {goal}\n  profile: {sorted(profile)}")

    def approve(action):
        return ask_user(f"Agent wants to click '{action['label']}'. Allow? [y/N] ").strip().lower() == "y"

    notes = {}
    # A hard kill loses everything, including the answer; stop early enough to still write it.
    budget = int(os.environ.get("JEV_MAX_SECONDS", "600"))
    deadline = time.monotonic() + budget

    def finish(result):
        outcome = {k: result.get(k) for k in ("done", "missing", "evidence")}
        outcome["stop"] = (result.get("stop_reason") or {}).get("message")
        note_page(notes, result.get("page") or {})
        result["answer"] = write_answer(request, notes, outcome)
        report(f"ANSWER {result['answer']}")
        return result

    with Agent(start_url, goal, profile=profile, approve=approve, files=files) as agent:
        checked = set()
        verify_rounds = recoveries = 0

        def arrival_check():
            """Verify the page a fresh attempt lands on. A deep link or recovery URL may already be the goal page,
            and with no action taken yet the mid-run check never looked at it (Wikipedia wandered off its article)."""
            page_now = agent.state["page"]
            note_page(notes, page_now)
            checked.add(page_key(page_now))
            early = verify(goal, page_now)
            if early.get("done"):
                report(f"VERIFY arrival {early}")
                agent.stop("done", "verified_done", "The goal is visibly complete on this page.",
                           evidence=early.get("evidence"))

        arrival_check()
        while True:
            while agent.state["status"] not in {"done", "blocked"}:
                try:
                    for state in agent.run():
                        if time.monotonic() > deadline:
                            agent.stop(
                                "blocked",
                                "time_budget",
                                f"Used the {budget // 60}-minute time budget; answering with what was found so far.",
                            )
                            break
                        last = state["history"][-1] if state["history"] else {}
                        label = " ".join(str(last.get("action") or "").split())[:100]
                        report(f"{state['elapsed_ms']:>6} ms  {state['status']:<9} {label}")
                        # The fast model often reaches the goal and keeps going. Check when a new URL appears.
                        page_now = agent.state["page"]
                        note_page(notes, page_now)
                        if (
                            agent.state["status"] == "ready"
                            and last.get("page_changed")
                            and page_key(page_now) not in checked
                            and len(checked) < MAX_EARLY_CHECKS
                        ):
                            checked.add(page_key(page_now))
                            early = verify(goal, page_now)
                            if early.get("done"):
                                report(f"VERIFY early {early}")
                                agent.stop(
                                    "done",
                                    "verified_done",
                                    "The goal is visibly complete on this page.",
                                    evidence=early.get("evidence"),
                                )
                                break
                except MissingValue as missing:
                    prompt = f"The form needs your {missing.field}. What should I enter?"
                    report(f"❓ {prompt}")
                    val = ask_user(prompt)
                    profile[missing.field] = val
                    if agent.profile is not None:
                        agent.profile[missing.field] = val
                    agent.state["goal"] += f"\nUser provided {missing.field}: {val}"
            if agent.state.get("denied"):
                declined = f"User declined '{agent.state['denied']}'"
                return finish({"done": False, "missing": declined, **agent.snapshot()})
            if agent.state["status"] == "blocked":
                # A verifier must not overrule a refusal (BLOCKED, denied, crash). But a run that merely ran out of
                # road (loop, budget) may be sitting on the finished page, so check that before calling it a failure.
                stop = agent.state.get("stop_reason") or {}
                if stop.get("code") in GAVE_UP or stop.get("code") == "time_budget":
                    final = verify(goal, agent.state["page"])
                    report(f"VERIFY after {stop['code']} {final}")
                    if final.get("done"):
                        agent.stop("done", "verified_done", "The goal is visibly complete on this page.",
                                   evidence=final.get("evidence"), after=stop["code"])
                        return finish({**final, **agent.snapshot()})
                # A human check is the person's to solve. Ask once per allowance, then look again in the same tab.
                if stop.get("code") == "bot_check" and recoveries < MAX_RECOVERIES:
                    recoveries += 1
                    answer = ask_user(f"{stop.get('message')} Press Enter when done, or type 'skip'. ").strip()
                    if answer.lower() not in {"skip", "n", "no", "stop"}:
                        agent.restart(goal)
                        checked.clear()
                        arrival_check()
                        continue
                    # Nobody cleared it: the same information is usually on another site, so plan a route there.
                    walled = True
                else:
                    walled = False
                # Step 4: stuck is not the end. Plan another route and try again in the same tab.
                in_time = time.monotonic() < deadline
                if (walled or stop.get("code") in RECOVERABLE) and recoveries < MAX_RECOVERIES and in_time:
                    recoveries += 1
                    plan = plan_recovery(goal, agent, ask=ask)
                    report(f"RECOVER {recoveries}/{MAX_RECOVERIES} after {stop.get('code')}: {plan}")
                    if plan["action"] == "ask_user" and plan.get("question"):
                        answer = ask_user(f"{plan['question']} ").strip()
                        if answer:
                            agent.restart(f"{plan['goal']}\nUser said: {answer}", plan.get("url"))
                            checked.clear()
                            arrival_check()
                            continue
                    elif plan["action"] == "retry":
                        agent.restart(plan["goal"], plan.get("url"))
                        checked.clear()
                        arrival_check()
                        continue
                    stop = {**stop, "message": f"{stop.get('message')} Recovery: {plan.get('why') or 'gave up'}"}
                return finish({"done": False, "missing": stop.get("message") or "Agent blocked", **agent.snapshot()})
            page = agent.state["page"]
            if (agent.state.get("stop_reason") or {}).get("code") == "verified_done":
                check = {"done": True, "missing": "", "evidence": agent.state["stop_reason"].get("evidence")}
            else:
                check = verify(goal, page)
            report(f"VERIFY {check}")
            verify_rounds += 1
            if check.get("done") or verify_rounds >= rounds or time.monotonic() > deadline:
                break
            # Re-plan in place: same tab, same history, sharper goal.
            agent.state.update(
                goal=f"{goal}\nStill missing: {check.get('missing')}. If this page only summarises it, open the "
                "provider's own page linked from here (official site, application or product page).",
                status="ready",
            )
        return finish({**check, **agent.snapshot()})


def local_laya_port(default=8791):
    """The port of a local Laya server: from TYPESAFE_BASE_URL when it points at this machine, else LAYA_PORT."""
    parsed = urllib.parse.urlparse(os.environ.get("TYPESAFE_BASE_URL", ""))
    if parsed.hostname in {"127.0.0.1", "localhost"} and parsed.port:
        return parsed.port
    return int(os.environ.get("LAYA_PORT") or default)


def ensure_local_server(port=None):
    """Ensure the local distilled Laya server is serving if running in local mode.

    start.bat and .env may run Laya on another port (e.g. 8793). A local TYPESAFE_BASE_URL now counts as local
    whatever its port, so the server is still checked and auto-started instead of being treated as remote.
    """
    base = os.environ.get("TYPESAFE_BASE_URL", "").rstrip("/")
    port = port or local_laya_port()
    local = not base or urllib.parse.urlparse(base).hostname in {"127.0.0.1", "localhost"}
    if local:
        base = f"http://127.0.0.1:{port}"
        os.environ["TYPESAFE_BASE_URL"] = base
        os.environ.setdefault("TYPESAFE_API_KEY", "local")
        try:
            urllib.request.urlopen(f"{base}/v1/systemone", timeout=1)
            return
        except Exception:
            pass
        root = Path(__file__).resolve().parents[1]
        laya_dir = root.parent / "laya"
        py = laya_dir / ".venv" / "Scripts" / "python.exe"
        if not py.exists():
            py = Path(sys.executable)
        server_script = laya_dir / "laya-browser" / "laya_browser.py"
        model_dir = laya_dir / "laya-browser-vision"
        if not model_dir.exists():
            model_dir = laya_dir / "laya-browser-distilled"
        if not model_dir.exists():
            model_dir = laya_dir / "laya-browser"
        os.environ.setdefault("GEAR2_BACKEND", "laya")
        os.environ.setdefault("GEAR2_LAYA_URL", base)
        if server_script.exists():
            import subprocess

            subprocess.Popen(
                [str(py), str(server_script), "serve", "--port", str(port), "--model", str(model_dir)],
                cwd=str(laya_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(30):
                time.sleep(0.2)
                try:
                    urllib.request.urlopen(f"{base}/v1/systemone", timeout=1)
                    return
                except Exception:
                    continue


def interactive_shell():
    print("\n" + "=" * 60)
    print("🤖 Jev Hybrid Browser Assistant (Local Laya + Vision)")
    print("=" * 60)
    print("Type your task in plain English. Type 'exit' or 'q' to quit.\n")

    while True:
        try:
            task = input("What would you like me to do?\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break
        if not task:
            continue
        if task.lower() in ("exit", "quit", "q"):
            print("Goodbye!")
            break

        doc_in = input("Documents or PDFs for me to read? (optional, comma-separated, or Enter to skip):\n> ").strip()
        docs = [d.strip().strip('"').strip("'") for d in doc_in.split(",") if d.strip()]

        file_in = input("Files I should upload to forms? (optional, comma-separated, or Enter to skip):\n> ").strip()
        files = [f.strip().strip('"').strip("'") for f in file_in.split(",") if f.strip()]

        url_in = input("Starting website URL? (optional, or Enter to auto-detect):\n> ").strip()
        url = url_in if url_in else None

        print("\n🚀 Starting browser task...\n")
        try:
            result = run(task, docs, url=url, files=files)
            status = "✅ DONE" if result.get("done") else f"⚠️ NOT DONE: {result.get('missing')}"
            print(f"\n{status}\nFinal URL: {result['page']['url']}\n")
        except Exception as e:
            print(f"\n❌ Error during task: {e}\n")
        print("-" * 60 + "\n")


def main():
    load_environment()
    parser = argparse.ArgumentParser(description="Plan with a general LLM, act with local distilled Laya or Jev.")
    parser.add_argument("request", nargs="?", help="Task description. Omit for interactive chat mode.")
    parser.add_argument("--doc", action="append", default=[], help="Document or PDF for the planner to read.")
    parser.add_argument("--file", action="append", default=[], help="File the agent can upload to forms.")
    parser.add_argument("--url", help="Target URL (defaults to URL derived by planner).")
    parser.add_argument("--browser", choices=["chrome", "brave"], help="Browser to drive (default: auto).")
    parser.add_argument(
        "--local", action="store_true", help="Force the local distilled Laya engine (LAYA_PORT, default 8791)."
    )
    args = parser.parse_args()
    if args.browser:
        os.environ["BROWSER"] = args.browser
    if args.local:
        os.environ["TYPESAFE_BASE_URL"] = f"http://127.0.0.1:{local_laya_port()}"
        os.environ["TYPESAFE_API_KEY"] = "local"
    ensure_local_server()
    if not args.request:
        interactive_shell()
        return
    result = run(args.request, args.doc, url=args.url, files=args.file)
    print("DONE" if result.get("done") else f"NOT DONE: {result.get('missing')}", result["page"]["url"])
