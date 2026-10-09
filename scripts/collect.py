"""Run Jev over a task file and log every decision as distillation teacher data. Resumable.

uv run --env-file .env python scripts/collect.py artifacts/tasks.jsonl --out artifacts/teacher
One <task>.jsonl of (request, answers) per task, plus runs.jsonl with each run's outcome.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

from jev_ultrafast import Agent

parser = argparse.ArgumentParser()
parser.add_argument("tasks")
parser.add_argument("--out", default="artifacts/teacher")
parser.add_argument("--max-actions", type=int, default=20)
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
runs = out / "runs.jsonl"
done = {json.loads(line)["id"] for line in runs.open(encoding="utf-8")} if runs.exists() else set()

for line in open(args.tasks, encoding="utf-8"):
    task = json.loads(line)
    task_id = hashlib.sha256(f"{task['url']}\n{task['goal']}".encode()).hexdigest()[:16]
    if task_id in done:
        continue
    os.environ["JEV_LOG"] = str(out / f"{task_id}.jsonl")
    error, state = None, None
    try:
        # Sensitive clicks (submit/pay/send/...) are always declined: collection never commits anything.
        with Agent(task["url"], task["goal"], approve=lambda _action: False) as agent:
            try:
                for state in agent.run():
                    if len(state["history"]) >= args.max_actions:
                        raise RuntimeError("action cap")
                    if len(state["decisions"]) >= 2 * args.max_actions:
                        raise RuntimeError("decision cap (stale-page loop)")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            state = agent.snapshot()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    row = {
        "id": task_id, **task, "status": state["status"] if state else "error", "error": error,
        "decisions": len(state["decisions"]) if state else 0, "actions": len(state["history"]) if state else 0,
        "ms": state["elapsed_ms"] if state else 0, "final_url": state["page"]["url"] if state else None,
        "steps": [h["action"] + (f"={h['text']!r}" if h["text"] else "") for h in state["history"]] if state else [],
    }
    with runs.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(row["status"], row["decisions"], row["ms"], task["goal"][:90], (error or "")[:60], flush=True)
