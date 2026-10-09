"""Run one decision backend over held-out tasks; a fixed LLM judge checks the final page (same judge for every arm).

TYPESAFE_BASE_URL=http://127.0.0.1:8791 uv run --env-file .env python scripts/eval_holdout.py \
    artifacts/eval-tasks.jsonl --arm distilled-v2 --out artifacts/eval.jsonl
"""

import argparse
import json
import os

from jev_ultrafast import Agent
from jev_ultrafast.model import json_content, post_json
from jev_ultrafast.planner import VERIFY

parser = argparse.ArgumentParser()
parser.add_argument("tasks")
parser.add_argument("--arm", required=True)
parser.add_argument("--out", default="artifacts/eval.jsonl")
parser.add_argument("--max-actions", type=int, default=20)
args = parser.parse_args()
base = os.environ["TEXT_MODEL_BASE_URL"].rstrip("/")


def judge(goal, page):
    result = post_json(base + "/chat/completions", os.environ["TEXT_MODEL_API_KEY"], {
        "model": os.environ.get("JUDGE_MODEL", os.environ["TEXT_MODEL"]),
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": VERIFY}, {"role": "user", "content": json.dumps(
            {"goal": goal, "url": page["url"], "title": page["title"], "page": page["text"]}, ensure_ascii=False)}],
    })
    return json_content(result)


for line in open(args.tasks, encoding="utf-8"):
    task = json.loads(line)
    error, state, verdict = None, None, {}
    try:
        with Agent(task["url"], task["goal"], approve=lambda _action: False) as agent:
            try:
                for state in agent.run():
                    if len(state["history"]) >= args.max_actions:
                        raise RuntimeError("action cap")
                    if len(state["decisions"]) >= 2 * args.max_actions:
                        raise RuntimeError("decision cap")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            state = agent.snapshot()
            # The judge sees a fresh observation, not the agent's last cached page.
            verdict = judge(task["goal"], agent.browser.observe(screenshot=False))
    except Exception as exc:
        error = error or f"{type(exc).__name__}: {exc}"
    row = {
        "arm": args.arm, **task, "status": state["status"] if state else "error", "error": error,
        "judge_done": bool(verdict.get("done")), "missing": verdict.get("missing"),
        "passed": bool(state) and state["status"] == "done" and not error and bool(verdict.get("done")),
        "ms": state["elapsed_ms"] if state else 0, "decisions": len(state["decisions"]) if state else 0,
        "steps": [h["action"] for h in state["history"]] if state else [],
    }
    with open(args.out, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(args.arm, row["passed"], row["status"], row["ms"], task["goal"][:80], flush=True)
