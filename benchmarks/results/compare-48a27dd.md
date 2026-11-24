# Site benchmark: 48a27dd, planner mode

OpenCode Go still returns 503 (connection_cooldown), so this run and the 43e1b54 run used TEXT_MODEL=deepseek/deepseek-chat and GEAR2_MODEL=combo/claude-review (Opus), set in the process env. The 360da3c runs used go-code/go-fast, so the columns are not a clean A/B.

| task | 360da3c r1 (go) | 360da3c r2 (go) | 43e1b54 (ds/cr) | 48a27dd (ds/cr) |
|---|---|---|---|---|
| amazon-search | PASS verified_done | PASS verified_done | fail model_blocked | fail model_blocked |
| ebay-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| gmaps-directions | PASS done | PASS done | PASS verified_done | PASS done |
| booking-hotel-search | PASS done | PASS done | PASS done | PASS done |
| google-flights-search | PASS done | PASS done | PASS done | PASS done |
| youtube-search-open | PASS done | PASS done | PASS verified_done | PASS verified_done |
| wikipedia-article | PASS verified_done | PASS verified_done | PASS verified_done | PASS done |
| bbc-news-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS done |
| weather-london | fail error | PASS done | fail verified_done | fail verified_done |
| allrecipes-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS done |
| github-repo-search | PASS verified_done | PASS verified_done | PASS bot_check | PASS verified_done |
| reddit-search | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| practice-form-fill | PASS done | PASS done | PASS verified_done | fail error |
| datepicker-set-date | PASS done | PASS done | PASS verified_done | PASS verified_done |
| stackoverflow-search | fail bot_check | fail bot_check | fail verified_done | fail bot_check |
| imdb-title-lookup | PASS verified_done | PASS verified_done | fail done | PASS verified_done |
| currency-convert-usd-eur | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| dictionary-definition | PASS verified_done | PASS verified_done | PASS verified_done | PASS verified_done |
| **total** | 16/18 | 17/18 | 14/18 | 14/18 |

## Verifier audit (48a27dd, checked against results.jsonl)
- **Wrong verified_done, weather:** accepted `weather.com/gb/england/greater-london/locality/westminster/today` with evidence "Westminster Weather / Westminster, London, England, United Kingdom …". The new rule that a district page does not count as the city did not hold. "London" appears in the district's breadcrumb, which is apparently enough for the verifier.
- **Correct page rejected, allrecipes:** /search?q=banana+bread was rejected with "Verifier evidence is not on the page.". The verifier's evidence was one multi-line quote ("Search Results For\nJanet's Rich Banana Bread\nBest Ever Banana Bread…") stitched across separate text lines, and evidence_holds fails on it. The benchmark passes it.
- github: verified_done on the real results page. The bot_wall false positive is fixed.
- imdb: verified_done on /title/tt0133093. The outcome-not-steps fix works.
- amazon: the /checkpoint CAPTCHA page gave model_blocked, not bot_check, so the CAPTCHA detector misses Amazon's checkpoint.

## Errors
- **practice-form:** crashed with "RuntimeError: Model provider returned HTTP 520; no action executed." 520 is not in the retry set (429/502/503/529) and is not caught in the agent loop.
- stackoverflow: a clean bot_check at /nocaptcha.

Same score as 43e1b54 (14/18) on the same models. imdb and github improved, weather is still accepted wrongly, allrecipes is now a verifier false negative (it still passes the benchmark), and practice-form was lost to a provider 520.
