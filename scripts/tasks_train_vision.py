"""Select 60 diverse non-holdout training tasks for fast Vision-Laya distillation."""

import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

HOLDOUT = [
    "en.wikipedia.org",
    "scenario=travel",
    "quotes.toscrape.com",
    "arxiv.org",
    "huggingface.co",
    "docs.python.org",
]

by_domain = defaultdict(list)
for line in open("artifacts/tasks.jsonl", encoding="utf-8"):
    t = json.loads(line.strip())
    url = t["url"]
    if any(h in url for h in HOLDOUT):
        continue
    domain = urlparse(url).netloc
    by_domain[domain].append(t)

selected = []
# Pick 2 tasks per domain across 30 domains = 60 tasks
for domain, tasks in sorted(by_domain.items(), key=lambda kv: -len(kv[1])):
    selected.extend(tasks[:2])
    if len(selected) >= 60:
        break

out_path = Path("artifacts/tasks-train-vision.jsonl")
with out_path.open("w", encoding="utf-8") as f:
    for t in selected:
        f.write(json.dumps(t, ensure_ascii=False) + "\n")

print(f"Selected {len(selected)} tasks across {len(set(urlparse(t['url']).netloc for t in selected))} domains -> {out_path}")
