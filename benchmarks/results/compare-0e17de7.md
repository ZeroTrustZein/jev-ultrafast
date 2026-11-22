# Site benchmark: 1dff5c7 vs 0e17de7

runs: 1dff5c7 20261122-213150-agent, 20261122-214129-planner | 0e17de7 20261122-215900-agent, 20261122-221127-planner

| task | 1dff5c7 agent | 1dff5c7 planner | 0e17de7 agent | 0e17de7 planner |
|---|---|---|---|---|
| amazon-search | fail done (2st) | fail done (2st) | fail done (2st) | PASS verified_done (1st) |
| ebay-search | fail done (7st) | fail done (4st) | fail model_blocked (0st) | PASS verified_done (2st) |
| gmaps-directions | PASS call_budget (56st) | PASS call_budget (55st) | PASS loop (54st) | PASS verified_done (60st) |
| booking-hotel-search | fail step_limit (60st) | PASS done (3st) | fail step_limit (60st) | fail done (1st) |
| google-flights-search | fail step_limit (60st) | fail step_limit (60st) | fail bench_missing_value (4st) | fail step_limit (60st) |
| youtube-search-open | fail done (3st) | fail done (4st) | fail done (23st) | fail done (1st) |
| wikipedia-article | PASS done (2st) | fail step_limit (60st) | PASS done (2st) | PASS verified_done (2st) |
| bbc-news-search | fail bench_missing_value (3st) | fail loop (11st) | fail bench_missing_value (2st) | fail loop (17st) |
| weather-london | PASS done (3st) | PASS verified_done (1st) | fail done (3st) | fail verified_done (2st) |
| allrecipes-search | fail done (10st) | PASS verified_done (1st) | fail done (10st) | PASS verified_done (2st) |
| github-repo-search | fail call_budget (2st) | PASS done (2st) | fail call_budget (2st) | PASS done (1st) |
| reddit-search | fail loop (6st) | PASS call_budget (0st) | fail call_budget (9st) | fail call_budget (1st) |
| practice-form-fill | PASS bench_missing_value (5st) | PASS loop (7st) | PASS bench_missing_value (3st) | PASS loop (7st) |
| datepicker-set-date | fail loop (3st) | fail loop (3st) | fail step_limit (60st) | fail step_limit (60st) |
| stackoverflow-search | PASS loop (20st) | fail loop (15st) | fail step_limit (60st) | fail step_limit (60st) |
| imdb-title-lookup | fail done (2st) | PASS done (3st) | fail done (7st) | PASS verified_done (1st) |
| currency-convert-usd-eur | fail done (1st) | fail done (32st) | fail done (1st) | fail done (3st) |
| dictionary-definition | PASS done (4st) | PASS verified_done (1st) | PASS done (4st) | PASS verified_done (1st) |
| **total** | 6/18 | 9/18 | 4/18 | 9/18 |

Counting the weather check artifact (below), the real planner score is 10/18. The real agent-mode score is 4/18, with imdb and ebay regressing (see below).

## verified_done audit (planner run; fleet scout verdicts, each one checked against results.jsonl/stop_log.jsonl)
- **WRONG PAGE: gmaps-directions.** It stopped with `verified_done` and `after: "step_limit"` on `/maps/dir/Eiffelturm,.../48.8583701,2.2919064/`: a route from the Eiffel Tower to its own coordinates (6 min, 400 m). "Louvre" appears nowhere in the URL or text. The evidence quoted was ["Eiffelturm", "48.8583701, 2.2919064", "6 min 400 m über Quai Jacques Chirac"], which shows the verifier accepted a route without checking the destination. The benchmark check is also too loose (text_any includes "Eiffel").
- Correct: amazon ("1-16 of over 50,000 results for \"usb-c cable\""), ebay ("25,000+ results for vintage film camera"), wikipedia, weather (London tenday page), allrecipes (/search?q=banana+bread), imdb (/title/tt0133093), dictionary.
- **No correct page was rejected.** github-repo-search reached the correct page with verified_done=true (last run's false negative is fixed), but the stop code is `done`. The scout misread this as a rejection.
- Correct rejections: booking (home page), youtube (results page, not /watch), xe (/currencyconverter with no conversion), reddit (/answers AI page, not /search).

## Benchmark artifact
- weather-london: the tightened check in sites.json requires `weather.com/weather/`, but weather.com's real London forecast URL is `/gb/england/greater-london/city/london/tenday` ("London Daily Forecast / London, England, United Kingdom"). That makes the correct page fail in both modes. Fix the check to url_any [`/weather/`, `/city/london/`] plus text "London, England".

## Loops
- datepicker: NOT fixed. It is now step_limit (60) in both modes instead of a 3-step loop. The planner pages month by month from Oct 2026 and never reaches March 2027. The agent drifted to demoqa.com/.
- reddit: NOT fixed. call_budget in both modes. The agent ended on an r/MadeMeSmile wiki page; the planner ended on reddit.com/answers/ (the AI answers page, not search).
- gmaps: agent mode still loops ("Repeated: Ziel Louvre, 75001", 54 steps). Planner mode ends in a false verified_done (above).

## Agent-mode regressions vs 1dff5c7 (6 → 4)
- ebay: model_blocked at step 0 with empty page text (the empty-page symptom is back on eBay).
- imdb: false DONE on the actor page /name/nm0000206 instead of the film.
- weather: check artifact (above).
- stackoverflow: last run's "pass" was the false Cloudflare pass; it is now step_limit on a random question.

## Top remaining causes (0e17de7, both modes)
1. step_limit (6): "Reached the 60-action limit without finishing the goal." (date picker in both modes, flights, stackoverflow in both modes, booking agent)
2. False DONE in agent mode (6): "The agent decided the goal is complete." (amazon checkpoint, youtube results, allrecipes A-Z, imdb actor page, xe home, weather is the artifact). Every one has gear 2 with model_reason "low confidence", so the vision fallback is the one declaring done.
3. call_budget (3): "Stopped after 120 model decisions without finishing (limit 120)." (reddit in both modes, github agent)
4. Sign-in walls / missing profile (4): "form asked for 'E-Mail oder Telefonnummer'" (bbc in both modes → accounts.google.com), flights "Where else?", practice form "Current Address".
