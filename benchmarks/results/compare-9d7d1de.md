# Site benchmark: 9d7d1de (PR #6), planner mode

Settings (process env only, .env untouched): TEXT_MODEL=combo/go-code, GEAR2_MODEL=combo/go-fast (Claude Haiku 5.5; an image probe confirmed it reads screenshots).
- r1 (20261123-194713-planner) and r2 (20261123-204820-planner): GEAR2_BACKEND=laya from .env, so unsure Laya steps escalate to GEAR2_MODEL.
- r3 (20261123-214024-planner): GEAR2_BACKEND=vlm, so gear 2 is always GEAR2_MODEL.
Baseline: 7462309 r1/r2 (TEXT_MODEL=combo/go-code, Laya-only gear 2).

| task | 7462309 r1 | 7462309 r2 | 9d7d1de r1 | 9d7d1de r2 | 9d7d1de r3 vlm |
|---|---|---|---|---|---|
| amazon-search | fail done | fail done | fail verified_done | fail done | fail verified_done |
| ebay-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| gmaps-directions | PASS verified_done | PASS verified_done | PASS verified_done | PASS done | PASS verified_done |
| booking-hotel-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS done | PASS done |
| google-flights-search | fail done | fail loop | PASS done | PASS done | PASS done |
| youtube-search-open | fail done | fail done | PASS verified_done | PASS verified_done | PASS done |
| wikipedia-article | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| bbc-news-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| weather-london | PASS verified_done | fail verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| allrecipes-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| github-repo-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| reddit-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done | PASS done |
| practice-form-fill | fail call_budget | PASS done | fail bench_timeout | fail bench_timeout | PASS done |
| datepicker-set-date | fail call_budget | fail done | PASS done | PASS verified_done | fail bench_exception |
| stackoverflow-search | fail loop | fail loop | fail bot_check | fail bot_check | fail bench_exception |
| imdb-title-lookup | PASS verified_done | PASS verified_done | fail bench_timeout | PASS verified_done | fail bench_exception |
| currency-convert-usd-eur | fail done | PASS verified_done | fail bench_timeout | fail bench_timeout | fail bench_exception |
| dictionary-definition | fail done | PASS verified_done | PASS verified_done | PASS verified_done | fail bench_exception |
| **total** | 10/18 | 12/18 | 13/18 | 14/18 | 12/18 |

## Verifier audit (checked against results.jsonl)
- **amazon (r1, r2, r3): questionable verified_done.** It stopped on `amazon.com/s/ref=nb_sb_noss_1?...field-keywords=usb-c cable`, a real search URL, but the observed text is only the header (144 chars: "Deliver to Germany … Sell") with no results rendered. The evidence is just the search box and the title: ["Search Amazon: usb-c cable", "Amazon.com : usb-c cable"]. The benchmark check also fails it because it expects `/s?k=`. Both are wrong in different ways: the verifier accepted without results, and the check is too strict on the URL form.
- **flights r1: benchmark PASS, verifier rejected.** The final URL is /travel/flights/search with the 2027-01-15 tfs. The verifier marked "Departure date 2027-01-15" as unmet. The page shows "Fri, Jan 15" with no year, so this is a strict-quote false negative.
- **practice-form r3: verifier rejected a filled form.** The fields were quoted ("First Name: Test", ...) but done=false on a later requirement. The benchmark passes it.
- No other wrong verified_done. weather, wikipedia, bbc, github, reddit, ebay, gmaps, booking, allrecipes, dictionary and imdb were accepted on correct pages.

## New-code behaviour
- **bot_check works:** stackoverflow in r1 and r2 stopped cleanly with "stackoverflow.com is showing a human-verification check ("CAPTCHA"). I don't solve those. Please complete it …" at /nocaptcha. No bypass attempt.
- **Escalation errors: none seen.** No traceback in any result row. Toggle hiding produced no crashes.
- **Slower steps:** with escalation to Haiku, several tasks exceeded the 600 s per-task cap (practice-form in r1 and r2, imdb in r1, xe in r1 and r2). The child printed nothing before the kill, so these are timeouts, not errors. Booking took 284 s.
- **r3 (vlm) infra:** the last 5 tasks (date picker, stackoverflow, imdb, xe, dictionary) crashed with "Model provider returned HTTP 503; no action executed". The runner's guard only counts 429s, so it did not stop the run. r3 is 12/13 on the tasks that ran.
