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
import sys
import time
import urllib.request
from pathlib import Path

from .agent import Agent
from .demo import load_environment
from .model import MissingValue, json_content, post_json

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
- goal: state every requirement explicitly (fields to fill, choices to make, where to stop). Include concrete values.
  Say to stop before any final submission or payment unless the user explicitly asked to submit or pay.
- profile: flat facts from the documents the agent may need to type (names, dates, numbers, addresses, emails).
  Copy values exactly. Never invent personal information; omit facts you do not have."""

VERIFY = """You check whether a browser agent finished its goal. Given the goal and the final page, return JSON:
{"done": true/false, "missing": "what is still not satisfied, or empty"}. Judge only visible evidence."""


def ask(system, content):
    base = (os.environ.get("PLANNER_BASE_URL") or os.environ.get("TEXT_MODEL_BASE_URL", "http://127.0.0.1:20128")).rstrip("/")
    key = os.environ.get("PLANNER_API_KEY") or os.environ.get("TEXT_MODEL_API_KEY", "local")
    model = os.environ.get("PLANNER_MODEL") or os.environ.get("TEXT_MODEL", "claude-odysseus")
    result = post_json(base + "/chat/completions", key, {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
    })
    return json_content(result)


def documents(paths):
    """Text files and PDF text inline; images as data URLs (multimodal planners read them directly)."""
    parts = []
    for path in map(Path, paths):
        kind = mimetypes.guess_type(path.name)[0] or ""
        if kind.startswith("image/"):
            data = base64.b64encode(path.read_bytes()).decode()
            parts += [{"type": "text", "text": f"Document {path.name}:"},
                      {"type": "image_url", "image_url": {"url": f"data:{kind};base64,{data}"}}]
        elif path.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
            if not text.strip():
                raise ValueError(f"{path.name} has no text layer (scanned). Pass a photo/screenshot of it instead.")
            parts.append({"type": "text", "text": f"Document {path.name} (PDF):\n{text[:40000]}"})
        else:
            parts.append({"type": "text", "text": f"Document {path.name}:\n{path.read_text(encoding='utf-8')}"})
    return parts


def run(request, docs=(), *, url=None, files=(), ask_user=input, report=print, rounds=3):
    context_lines = []
    if url:
        context_lines.append(f"Current open page in browser: {url}")
    if files:
        context_lines.append(f"Files the agent can attach to upload fields: {[Path(f).name for f in files]}")
    note = ("\n" + "\n".join(context_lines)) if context_lines else ""
    plan = ask(PLAN, [{"type": "text", "text": request + note}, *documents(docs)])
    profile, goal = dict(plan.get("profile") or {}), plan["goal"]
    start_url = plan.get("url") or url or "https://html.duckduckgo.com/html/"
    report(f"PLAN {start_url}\n  goal: {goal}\n  profile: {sorted(profile)}")

    def approve(action):
        return ask_user(f"Agent wants to click '{action['label']}'. Allow? [y/N] ").strip().lower() == "y"

    with Agent(start_url, goal, profile=profile, approve=approve, files=files) as agent:
        for _ in range(rounds):
            while agent.state["status"] not in {"done", "blocked"}:
                try:
                    for state in agent.run():
                        last = state["history"][-1] if state["history"] else {}
                        report(f"{state['elapsed_ms']:>6} ms  {state['status']:<9} {last.get('action', '')}")
                except MissingValue as missing:
                    profile[missing.field] = ask_user(f"What should go in '{missing.field}'? ")
            if agent.state.get("denied"):
                return {"done": False, "missing": f"User declined '{agent.state['denied']}'", **agent.snapshot()}
            if agent.state["status"] == "blocked":
                # A verifier must not overrule the agent's own BLOCKED; only a DONE gets checked.
                return {"done": False, "missing": "Agent blocked", **agent.snapshot()}
            page = agent.state["page"]
            check = ask(VERIFY, json.dumps({"goal": goal, "url": page["url"], "page": page["text"]}))
            report(f"VERIFY {check}")
            if check.get("done"):
                break
            # Re-plan in place: same tab, same history, sharper goal.
            agent.state.update(goal=f"{goal}\nStill missing: {check.get('missing')}", status="ready")
        return {**check, **agent.snapshot()}


def ensure_local_server(port=8791):
    """Ensure the local distilled Laya server is serving if running in local mode."""
    base = os.environ.get("TYPESAFE_BASE_URL", "").rstrip("/")
    if not base and not os.environ.get("TYPESAFE_API_KEY"):
        os.environ["TYPESAFE_BASE_URL"] = f"http://127.0.0.1:{port}"
        os.environ["TYPESAFE_API_KEY"] = "local"
        base = f"http://127.0.0.1:{port}"
    if f":{port}" in base:
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
        model_dir = laya_dir / "laya-browser-distilled"
        if not model_dir.exists():
            model_dir = laya_dir / "laya-browser"
        if server_script.exists():
            import subprocess
            subprocess.Popen([str(py), str(server_script), "serve", "--port", str(port), "--model", str(model_dir)],
                             cwd=str(laya_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    parser.add_argument("--local", action="store_true", help="Force local distilled Laya engine on port 8791.")
    args = parser.parse_args()
    if args.browser:
        os.environ["BROWSER"] = args.browser
    if args.local:
        os.environ["TYPESAFE_BASE_URL"] = "http://127.0.0.1:8791"
        os.environ["TYPESAFE_API_KEY"] = "local"
    ensure_local_server()
    if not args.request:
        interactive_shell()
        return
    result = run(args.request, args.doc, url=args.url, files=args.file)
    print("DONE" if result.get("done") else f"NOT DONE: {result.get('missing')}", result["page"]["url"])
