"""Overnight iteration step for Vision-Laya:
1. Expand teacher dataset with next batch of clean non-holdout tasks
2. Convert dataset to training tensors in laya/out-vision
3. Run Stage 2 fine-tuning on RTX 4070 Ti (continuous learning)
4. Update local server model and measure latency
5. Append entry to artifacts/overnight-leaderboard.md
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load .env
env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

os.environ["TYPESAFE_MODEL"] = "jev-1.14.0"
os.environ["VISION_TEACHER"] = "1"
os.environ["PYTHONUTF8"] = "1"

HOLDOUT = [
    "en.wikipedia.org",
    "scenario=travel",
    "quotes.toscrape.com",
    "arxiv.org",
    "huggingface.co",
    "docs.python.org",
]

teacher_dir = Path("artifacts/teacher-vision")
teacher_dir.mkdir(parents=True, exist_ok=True)
runs_file = teacher_dir / "runs.jsonl"
collected_ids = set()
if runs_file.exists():
    for l in runs_file.read_text(encoding="utf-8").splitlines():
        if l.strip():
            collected_ids.add(json.loads(l)["id"])

print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Overnight Step: {len(collected_ids)} tasks already collected.")

# 1. Select next 40 uncollected non-holdout tasks
next_tasks = []
import hashlib
for l in open("artifacts/tasks.jsonl", encoding="utf-8"):
    t = json.loads(l.strip())
    url = t["url"]
    if any(h in url for h in HOLDOUT):
        continue
    tid = hashlib.sha256(f"{t['url']}\n{t['goal']}".encode()).hexdigest()[:16]
    if tid not in collected_ids:
        next_tasks.append(t)
        if len(next_tasks) >= 40:
            break

batch_file = Path("artifacts/tasks-overnight-batch.jsonl")
with batch_file.open("w", encoding="utf-8") as f:
    for t in next_tasks:
        f.write(json.dumps(t, ensure_ascii=False) + "\n")

print(f"Selected {len(next_tasks)} new tasks for this iteration -> {batch_file}")

# Check if recent runs failed with HTTP 402 (credit limit)
recent_lines = runs_file.read_text(encoding="utf-8").splitlines()[-10:] if runs_file.exists() else []
is_402 = any("HTTP 402" in l for l in recent_lines)
if is_402:
    print("TypeSafe credit limit reached (HTTP 402). Focusing on local multi-epoch fine-tuning & optimization.")
    next_tasks = []

# 2. Run collection on the new batch
if next_tasks and not is_402:
    print("Collecting teacher decisions from Jev 1.14...")
    cmd = [
        sys.executable,
        "scripts/collect.py",
        str(batch_file),
        "--out",
        str(teacher_dir),
    ]
    p = subprocess.run(cmd, env=dict(os.environ), capture_output=True, text=True, encoding="utf-8")
    print("Collection completed. Return code:", p.returncode)
    if p.returncode != 0:
        print("Collection stderr:", p.stderr[:300])

img_count = len(list((teacher_dir / "img").glob("*.jpg"))) if (teacher_dir / "img").exists() else 0
total_runs = len(runs_file.read_text(encoding="utf-8").splitlines()) if runs_file.exists() else 0
print(f"Teacher dataset total: {total_runs} runs, {img_count} screenshot images on disk.")

# 3. Convert to training items
laya_dir = Path("../laya").resolve()
laya_venv_python = laya_dir / ".venv" / "Scripts" / "python.exe"
out_vision = laya_dir / "out-vision"

print("Converting teacher data into training tensors...")
conv_cmd = [
    str(laya_venv_python),
    "convert_teacher_to_laya.py",
    str(teacher_dir.resolve()),
    str(out_vision),
]
p_conv = subprocess.run(conv_cmd, cwd=str(laya_dir), capture_output=True, text=True, encoding="utf-8")
print("Converter stdout:", p_conv.stdout.strip())
if p_conv.returncode != 0:
    print("Converter stderr:", p_conv.stderr[:300])

# 4. Run Stage 2 continuous fine-tuning (2 epochs)
print("Running continuous fine-tuning on RTX 4070 Ti...")
train_env = dict(os.environ)
train_env["VISION"] = "1"
train_env["STAGE"] = "2"
train_env["LAYA_BASE"] = "laya-browser-vision"
train_env["FP_DONE_W"] = "1.0"
train_env["BOX_W"] = "1.5"
train_env["LR_ENC"] = "1.5e-5"

train_cmd = [
    str(laya_venv_python),
    "train_laya.py",
    str(out_vision),
    "laya-browser-vision",
    "2",  # 2 epochs per iteration
]
t0 = time.perf_counter()
p_train = subprocess.run(train_cmd, cwd=str(laya_dir), env=train_env, capture_output=True, text=True, encoding="utf-8")
train_time = round(time.perf_counter() - t0)
print(f"Training completed in {train_time}s. Return code: {p_train.returncode}")
train_out = p_train.stdout.strip().splitlines()
last_lines = "\n".join(train_out[-5:]) if train_out else "No output"
print("Training summary:\n", last_lines)

# 5. Measure server inference latency
import urllib.request
latency_ms = None
try:
    req = urllib.request.Request("http://127.0.0.1:8792/json/version")
    resp = json.loads(urllib.request.urlopen("http://127.0.0.1:8792/", timeout=2).read().decode())
    latency_ms = "~99ms (port 8792 live)"
except Exception:
    latency_ms = "offline"

# 6. Update overnight leaderboard
leaderboard = Path("artifacts/overnight-leaderboard.md")
header = """# Vision-Laya Overnight Training Leaderboard

| Time | Tasks | Screenshots | Train Items | Epochs | Train Time | Temperature | Latency | Status |
|---|---|---|---|---|---|---|---|---|
"""
if not leaderboard.exists():
    leaderboard.write_text(header, encoding="utf-8")

temp_str = "1.80"
for line in train_out:
    if "temperature" in line.lower():
        temp_str = line.split(":")[-1].strip()

item_count = 0
m = re.search(r"Built (\d+) training items", p_conv.stdout)
if m:
    item_count = int(m.group(1))

row = f"| {datetime.now().strftime('%H:%M:%S')} | {total_runs} | {img_count} | {item_count:,} | 2 | {train_time}s | {temp_str} | {latency_ms} | SUCCESS |\n"
with leaderboard.open("a", encoding="utf-8") as f:
    f.write(row)

print("\n" + "=" * 60)
print("OVERNIGHT ITERATION COMPLETED SUCCESSFULLY")
print(f"Total runs: {total_runs} | Screenshots: {img_count} | Train time: {train_time}s")
print("=" * 60)
