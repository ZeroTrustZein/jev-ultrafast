"""Cost-gate: run 20 live Jev 1.14 image decisions to measure token usage, latency, and projected cost."""

import json
import os
import statistics
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load .env without printing secrets
env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

os.environ["TYPESAFE_MODEL"] = "jev-1.14.0"
os.environ["VISION_TEACHER"] = "1"
os.environ["JEV_LOG"] = "artifacts/cost_gate.jsonl"
os.environ["GEAR2_BACKEND"] = "jev"

from jev_ultrafast import Agent

log_path = Path("artifacts/cost_gate.jsonl")
if log_path.exists():
    log_path.unlink()

tasks_file = Path("artifacts/eval-tasks.jsonl")
tasks = [json.loads(line) for line in tasks_file.read_text(encoding="utf-8").splitlines() if line.strip()]

calls = []
print("Starting 20-call cost gate on live Jev 1.14 API...")

step_count = 0
for task_idx, task in enumerate(tasks):
    if step_count >= 20:
        break
    url, goal = task["url"], task["goal"]
    print(f"\nTask {task_idx+1}: {goal[:50]}... ({url})")
    try:
        with Agent(url, goal, approve=lambda _: False) as agent:
            for state in agent.run():
                step_count += 1
                decisions = state.get("decisions", [])
                if decisions:
                    d = decisions[-1]
                    usage = d.get("usage", {})
                    lat = d.get("latency_ms", 0)
                    calls.append({
                        "step": step_count,
                        "op": d.get("operation"),
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                        "latency_ms": lat,
                    })
                    print(f"  Step {step_count}/20: op={d.get('operation')} input_tokens={usage.get('input_tokens')} lat={lat}ms")
                if step_count >= 20:
                    break
    except Exception as exc:
        print(f"  Task {task_idx+1} ended early: {type(exc).__name__}: {exc}")

if not calls:
    print("No calls recorded.")
    exit(1)

inputs = [c["input_tokens"] for c in calls if c["input_tokens"]]
outputs = [c["output_tokens"] for c in calls if c["output_tokens"]]
latencies = [c["latency_ms"] for c in calls if c["latency_ms"]]

print("\n" + "=" * 60)
print(f"COST GATE REPORT: {len(calls)} live Jev 1.14 calls")
print("=" * 60)
if inputs:
    print(f"Input tokens : min={min(inputs)}, max={max(inputs)}, mean={statistics.mean(inputs):.1f}, median={statistics.median(inputs):.1f}")
if outputs:
    print(f"Output tokens: min={min(outputs)}, max={max(outputs)}, mean={statistics.mean(outputs):.1f}, median={statistics.median(outputs):.1f}")
if latencies:
    print(f"Latency (ms) : min={min(latencies)}, max={max(latencies)}, mean={statistics.mean(latencies):.1f}, median={statistics.median(latencies):.1f}")

# Check logged lines
if log_path.exists():
    lines = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    has_image = sum(1 for l in lines if l.get("image"))
    has_box = sum(1 for l in lines if l.get("answers", {}).get("vision_box"))
    has_rects = sum(1 for l in lines if l.get("rects"))
    print(f"\nLog integrity: {len(lines)} entries in {log_path}")
    print(f"  - With screenshot file: {has_image}/{len(lines)}")
    print(f"  - With vision_box answer: {has_box}/{len(lines)}")
    print(f"  - With element rects: {has_rects}/{len(lines)}")

avg_inp = statistics.mean(inputs) if inputs else 1500
# At $42 per 1 billion input tokens (TypeSafe reported rate = $0.042 / 1M tokens)
cost_per_call = (avg_inp / 1_000_000_000) * 42.0
cost_10k = cost_per_call * 10_000

print(f"\nCost projection (at $42 / 1B tokens):")
print(f"  - Per decision call: ${cost_per_call:.6f}")
print(f"  - 1,000 teacher calls: ${cost_per_call * 1_000:.4f}")
print(f"  - 10,000 teacher calls (full dataset): ${cost_10k:.2f}")
print("=" * 60)
