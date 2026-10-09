# Local decision backend (experimental)

`JEV_BACKEND=local` replaces the TypeSafe request with one Ollama call (`jev_ultrafast/local.py`). Same request body in,
same answer shape out. A JSON-schema `anyOf` grammar forces the operation first and then only a target that belongs to
that operation. Confidence is the model's own log-probability over the chosen values.

## Results, 2026-10-08 (RTX 4070 Ti 12 GB, Ollama 0.34, `scripts/bench.py`)

Text helper for every row: local `qwen3:4b-instruct`. Independent checks as in `scripts/bench.py`.

| Decision backend | Fixture | Wikipedia | Median decision |
| --- | --- | --- | ---: |
| TypeSafe Jev | 0/3 (all steps right; helper typed `LISBON`) | **3/3**, ~2.6 s | 240–285 ms |
| TypeSafe Jev + `qwen2.5-coder:1.5b` helper | **3/3**, ~2.2 s | — | 245 ms |
| qwen3:4b-instruct | 0/2 | 0/2 (reaches article, never DONE) | 550–700 ms |
| qwen3:8b (no thinking) | 0/2 (skips "Find stays") | 0/2 (reaches article, never DONE) | 500–775 ms |
| gemma3:4b | 0/2 | 0/2 | 480–615 ms |
| llama3.2:3b | 0/2 | 0/2 | 320–480 ms |

Without the `done`/`next` reasoning fields, a local decision takes 140–270 ms, faster than Jev, but quality is
worse still. Off-the-shelf small models do not follow multi-step goals reliably: they skip submit buttons and do
not choose DONE.

Small local text helpers also copy page text (`LISBON`, `LISBON · DESIGN`) instead of the goal's value.

## Next step that should work: distill Jev

Log Jev's `(request, answer)` pairs across many tasks, then QLoRA-fine-tune `qwen3:4b` on them (fits 12 GB).
That model learns Jev's policy instead of being prompted toward it.
