From the repository root, serve fixtures with `python -m http.server 8000`.
Run Suite B with `uv run --env-file .env python scripts/eval_holdout.py artifacts/eval-vision.jsonl --arm <arm> --out artifacts/eval-vision-results.jsonl`.
The runner still needs the configured TypeSafe/text-model and judge services; tasks use `expect` to check final `#result` text.
Pages are static and deterministic; no fixture makes external requests.
