"""Compare decision backends on fixed tasks with independent checks. Backend comes from env (JEV_BACKEND, LOCAL_MODEL).

uv run --env-file .env python scripts/bench.py --task fixture --runs 3
"""

import argparse
import json
import os
import statistics
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.append(str(Path(__file__).resolve().parents[1]))
from examples.flights import GOALS as FLIGHTS_GOAL  # noqa: E402
from examples.flights import URL as FLIGHTS_URL  # noqa: E402
from examples.flights import verify as verify_flights  # noqa: E402
from jev_ultrafast import Agent  # noqa: E402

TASKS = {
    "fixture": (
        "http://127.0.0.1:8766/fixture.html?scenario=travel",
        "Use the destination search and filters to find Design stays in Lisbon with Free cancellation, "
        "then open Casa Flora.",
        lambda agent: (
            agent.state["page"]["url"].endswith("#casa-flora")
            and "Your filters: Design · Free cancellation enabled · Destination Lisbon"
            in agent.browser.evaluate("document.body.innerText")
        ),
    ),
    "wikipedia": (
        "https://en.wikipedia.org/wiki/Main_Page",
        "Find and open the Wikipedia article about Gödel’s incompleteness theorems.",
        lambda agent: (
            unquote(agent.browser.evaluate("location.href"))
            .replace("’", "'")
            .endswith("/wiki/Gödel's_incompleteness_theorems")
        ),
    ),
    "flights": (
        FLIGHTS_URL,
        FLIGHTS_GOAL,
        lambda agent: verify_flights(agent.browser.observe(screenshot=False))["passed"],
    ),
}

parser = argparse.ArgumentParser()
parser.add_argument("--task", choices=TASKS, required=True)
parser.add_argument("--runs", type=int, default=3)
parser.add_argument("--max-actions", type=int, default=25)
args = parser.parse_args()
url, goal, check = TASKS[args.task]
backend = (
    os.environ.get("LOCAL_MODEL", "qwen3:4b-instruct") if os.environ.get("JEV_BACKEND") == "local"
    else os.environ.get("TYPESAFE_BASE_URL", "jev")
)

for run in range(args.runs):
    error = None
    with Agent(url, goal) as agent:
        try:
            for state in agent.run():
                if len(state["history"]) >= args.max_actions:
                    raise RuntimeError("action cap")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        state = agent.snapshot()
        passed = state["status"] == "done" and not error and check(agent)
    latencies = [d["latency_ms"] for d in state["decisions"]]
    row = {
        "task": args.task,
        "backend": backend,
        "run": run + 1,
        "passed": passed,
        "ms": state["elapsed_ms"],
        "actions": len(state["history"]),
        "decisions": len(latencies),
        "median_decision_ms": statistics.median(latencies) if latencies else None,
        "status": state["status"],
        "error": error,
        "steps": [h["action"] + (f"={h['text']!r}" if h["text"] else "") for h in state["history"]],
        "final_url": state["page"]["url"],
    }
    print(json.dumps(row, ensure_ascii=False), flush=True)
    Path("artifacts").mkdir(exist_ok=True)
    with open("artifacts/bench.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
