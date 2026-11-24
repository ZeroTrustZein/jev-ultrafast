# Site benchmark: f07438e (PR #8), planner mode

Settings (process env only): TEXT_MODEL=combo/go-code, GEAR2_MODEL=combo/go-fast, GEAR2_BACKEND=laya from .env, 900 s per task.
**Only one run completed (20261123-231822-planner).** Its last 4 tasks (stackoverflow, imdb, xe, dictionary) crashed with "Model provider returned HTTP 503; no action executed". The runner's provider-error guard then aborted the series, so run 2 never started. A probe afterwards showed both combos down: "Service temporarily unavailable: every target in this combo is cooling down (model lockout, circuit breaker, provider or connection cooldown): opencode-go/deepseek-v4.1-flash…" and the same for opencode-go/claude-haiku-5-5.

r1 = 13/18, which is **13 of the 14 tasks not lost to the outage**. The 5 lost: 4 provider crashes plus a date-picker timeout at 900 s. The one real failure is stackoverflow (bot_check).

| task | 360da3c r1 | 360da3c r2 | f07438e r1 |
|---|---|---|---|
| amazon-search | PASS verified_done | PASS verified_done | PASS done |
| ebay-search | PASS verified_done | PASS verified_done | PASS verified_done |
| gmaps-directions | PASS done | PASS done | PASS verified_done |
| booking-hotel-search | PASS done | PASS done | PASS done |
| google-flights-search | PASS done | PASS done | PASS done |
| youtube-search-open | PASS done | PASS done | PASS done |
| wikipedia-article | PASS verified_done | PASS verified_done | PASS verified_done |
| bbc-news-search | PASS verified_done | PASS verified_done | PASS verified_done |
| weather-london | fail error | PASS done | PASS verified_done |
| allrecipes-search | PASS verified_done | PASS verified_done | PASS verified_done |
| github-repo-search | PASS verified_done | PASS verified_done | PASS verified_done |
| reddit-search | PASS verified_done | PASS verified_done | PASS verified_done |
| practice-form-fill | PASS done | PASS done | PASS done |
| datepicker-set-date | PASS done | PASS done | fail bench_timeout |
| stackoverflow-search | fail bot_check | fail bot_check | fail bench_exception |
| imdb-title-lookup | PASS verified_done | PASS verified_done | fail bench_exception |
| currency-convert-usd-eur | PASS verified_done | PASS verified_done | fail bench_exception |
| dictionary-definition | PASS verified_done | PASS verified_done | fail bench_exception |
| **total** | 16/18 | 17/18 | 13/18 |

## Verifier audit (r1, checked against results.jsonl)
- No wrong verified_done.
- **Correct pages still rejected (benchmark PASS, verifier no):**
  - practice-form: every field was quoted ("First Name: Test", "Last Name: User", "test@example.com", "Mobile Number: 5555555555", "option Maths, selected."). It was still rejected: "doesn't show that the Other gender radio is selected, or that any hobby checkbox … is checked — page only lists the option labels". The submit and pre-fill complaints from 360da3c are gone. The verifier now lacks radio and checkbox state in its field data.
  - booking: London results, "Fri, Jan 15", "Sun, Jan 17", "2 adults · 0 children · 1 room" were all quoted. It was rejected over the price range "€ 30 – € 600+". The verifier asked for something the goal didn't.
  - flights: the results list was quoted ("Virgin Atlantic … Nonstop €284", "Departure: Fri, Jan 15", "19 results returned") and still rejected.
- date picker: 900 s timeout, no result (bench_timeout).

## Dictionary diagnosis (instrumented run, reported separately)
The ~4-5 minutes are not the verifier and not retries: 365 POSTs, all 200, no Retry-After. PLAN started on the merriam-webster.com homepage. There the agent made 120 decisions with 0 executed actions (Laya ×2 + go-fast about 2 s each) until call_budget at 247 s. RECOVER then retried /dictionary/serendipity, and arrival verify passed at 258 s.
