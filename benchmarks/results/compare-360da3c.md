# Site benchmark: 360da3c (PR #7), planner mode

Settings (process env only, .env untouched): TEXT_MODEL=combo/go-code, GEAR2_MODEL=combo/go-fast, GEAR2_BACKEND=laya from .env (unsure steps escalate). The per-task cap is now 900 s.
Runs: 20261123-222320-planner (r1) and 20261123-224810-planner (r2). Baseline: 9d7d1de r1/r2.

| task | 9d7d1de r1 | 9d7d1de r2 | 360da3c r1 | 360da3c r2 |
|---|---|---|---|---|
| amazon-search | fail verified_done | fail done | PASS verified_done | PASS verified_done |
| ebay-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| gmaps-directions | PASS verified_done | PASS done | PASS done | PASS done |
| booking-hotel-search | PASS verified_done | PASS done | PASS done | PASS done |
| google-flights-search | PASS done | PASS done | PASS done | PASS done |
| youtube-search-open | PASS verified_done | PASS verified_done | PASS done | PASS done |
| wikipedia-article | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| bbc-news-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| weather-london | PASS verified_done | PASS verified_done | fail error | PASS done |
| allrecipes-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| github-repo-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| reddit-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| practice-form-fill | fail bench_timeout | fail bench_timeout | PASS done | PASS done |
| datepicker-set-date | PASS done | PASS verified_done | PASS done | PASS done |
| stackoverflow-search | fail bot_check | fail bot_check | fail bot_check | fail bot_check |
| imdb-title-lookup | fail bench_timeout | PASS verified_done | PASS verified_done | PASS verified_done |
| currency-convert-usd-eur | fail bench_timeout | fail bench_timeout | PASS verified_done | PASS verified_done |
| dictionary-definition | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| **total** | 13/18 | 14/18 | 16/18 | 17/18 |

## Verifier audit (checked against results.jsonl)
- **Thin-page rule: no false rejections.** "The page's main content has not loaded yet (only a header)." appears in 0 result rows in either run. Nothing that passed before was rejected as thin.
- **amazon: first real pass (r2).** It was verified_done on `amazon.com/s?k=usb-c+cable…` with results rendered. In r1 the page served an error, which was correctly rejected.
- **Correct page rejected, practice-form (r1, r2):** the benchmark passes it, and the verifier quoted every field ("First Name: Test", "Last Name: User", "test@example.com", "Mobile Number: 5555555555", "option Maths, selected."). It still said not done:
  - r1: "DOB got filled to 23 Nov 2026, which goal said to leave untouched". demoqa pre-fills today's date, so this is not the agent's doing.
  - r2: "form page also needs confirmation still unsent". That contradicts the goal, which says do not submit.
  This is an over-strict verifier, not an agent fault.
- **Correct pages rejected:** flights r1 (benchmark PASS, verifier no), date picker r1 (PASS with stop code done, not verified). Neither cost a pass under the benchmark check.
- No wrong verified_done in either run.

## Remaining failures
- stackoverflow, both runs: a clean bot_check stop at /nocaptcha ("I don't solve those"). Environment, not agent.
- weather r1: crashed with "_IPCResponseTimeout: Runtime.evaluate timed out after 5s waiting for the daemon" on the home page. This is a browser/CDP hiccup; it passed in r2.
- dictionary: passes, but takes 271-316 s on 0 steps (arrival verify). It is slow, not wrong.
