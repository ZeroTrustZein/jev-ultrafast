# Site benchmark: 697dab1 (requirement-list verifier, start/arrival verify, caption labels, ping-pong stop), planner mode

runs: 20261122-234640-planner (run 1) and 20261123-001238-planner (run 2), compared with b622574 (20261122-230535-planner + the 232502 rerun).

**Run 2 is void.** All 18 tasks crashed in about 2 s each with "RuntimeError: Model provider returned HTTP 429; no action executed." Run 1 also lost its last 4 tasks (stackoverflow, imdb, xe, dictionary), and the date picker crashed mid-recovery, all with the same 429. The rate limit came from a model provider reached through model.py post_json. The Laya decision server on 8793 was still answering 200.

**Run 1, 13 valid tasks: 6 PASS** (ebay, gmaps, booking, wikipedia, reddit, practice-form). On the same 13 tasks, b622574 passed 7.

| task | b622574 | 697dab1 run 1 | 697dab1 run 2 |
|---|---|---|---|
| amazon-search | fail done (CAPTCHA) | fail done (CAPTCHA, correctly rejected) | void (429) |
| ebay-search | PASS | PASS verified_done (3 st) | void |
| gmaps-directions | PASS | PASS verified_done (5 st; Eiffel→Louvre, "8 min 3,8 km") | void |
| booking-hotel-search | PASS | PASS verified_done on arrival (0 st) | void |
| google-flights-search | fail | fail call_budget (**the planner set the date to 2024-01-15** instead of 2027-01-15) | void |
| youtube-search-open | fail | fail done (results page, correctly rejected) | void |
| wikipedia-article | fail | PASS done (correct page **rejected by the verifier**, see below) | void |
| bbc-news-search | fail | fail done (Google sign-in) | void |
| weather-london | PASS | fail verified_done (home page, see below) | void |
| allrecipes-search | PASS | fail done (banana-bread category page, correctly rejected) | void |
| github-repo-search | PASS | fail call_budget (ended on github.com/microsoft/playwright) | void |
| reddit-search | fail | PASS verified_done on arrival (/search/) | void |
| practice-form-fill | fail | PASS loop (doubtful, the verifier says no field is filled) | void |
| datepicker-set-date | fail | crash 429 mid-recovery | void |
| stackoverflow / imdb / xe / dictionary | fail / PASS / fail / PASS | crash 429 | void |

## Verifier audit (run 1, checked against results.jsonl)
- **Correct page rejected: wikipedia-article.** The page was /wiki/Ada_Lovelace and the requirement "Open Ada Lovelace Wikipedia article" was met, quoting "Ada Lovelace - Wikipedia". It was still rejected with missing "Not shown: Read-only: do not edit or log in". The goal's prohibition was turned into a requirement that needs a quote, and a prohibition can never be quoted. The same pattern appears on bbc ("do not sign in", met: False) and on the date picker ("No changes to other fields").
- **Questionable verified_done: weather-london** on `weather.com/?Goto=Redirected`, the home page. Its main card is "London Weather / London, England, United Kingdom / Now 40°", but the location chip still reads Berlin, and it is not the forecast page the goal asks for. The verifier's requirement "stop on London forecast page" was marked met. The benchmark check fails it.
- Correct: ebay ("vintage film camera 35mm"), gmaps (both Eiffel and Louvre quoted), booking (every field quoted from the URL and page), reddit (on /search/ this time). Correct rejections: the amazon CAPTCHA, youtube results, the allrecipes category page, github repo page.

## Other findings
- **Date picker (probe on 697dab1):** the labels are fixed. e7 = {kind: fill, role: textbox, label: "Select Date", value: "11/22/2026"}, e8 = {kind: click, label: "Open Select Date"}. But an Agent pass still never chooses e7. Every step is gear 2: Date Picker (sidebar, no change) ×2 → logo → home ×2 → Forms → home → Forms → Practice Form, then call_budget after 9 steps. The ping-pong stop did not trigger, because the agent moved on to a third URL.
- The flights planner wrote the wrong year (2024-01-15) into the goal, and the recovery URL carried it forward.
