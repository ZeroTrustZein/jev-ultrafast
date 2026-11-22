# Site benchmark: 0e17de7 vs 5e559ac

runs: 0e17de7 20261122-215900-agent, 20261122-221127-planner | 5e559ac 20261122-222755-agent, 20261122-224516-planner

| task | 0e17de7 agent | 0e17de7 planner | 5e559ac agent | 5e559ac planner |
|---|---|---|---|---|
| amazon-search | fail done (2st) | PASS verified_done (1st) | fail done (2st) | fail done (2st) |
| ebay-search | fail model_blocked (0st) | PASS verified_done (2st) | fail done (7st) | fail done (2st) |
| gmaps-directions | PASS loop (54st) | PASS verified_done (60st) | fail call_budget (33st) | fail call_budget (28st) |
| booking-hotel-search | fail step_limit (60st) | fail done (1st) | fail step_limit (60st) | fail step_limit (60st) |
| google-flights-search | fail bench_missing_value (4st) | fail step_limit (60st) | fail step_limit (60st) | fail step_limit (60st) |
| youtube-search-open | fail done (23st) | fail done (1st) | PASS step_limit (60st) | fail done (1st) |
| wikipedia-article | PASS done (2st) | PASS verified_done (2st) | PASS done (2st) | PASS verified_done (1st) |
| bbc-news-search | fail bench_missing_value (2st) | fail loop (17st) | fail bench_missing_value (2st) | fail loop (11st) |
| weather-london | fail done (3st) | fail verified_done (2st) | PASS done (2st) | PASS verified_done (2st) |
| allrecipes-search | fail done (10st) | PASS verified_done (2st) | fail bench_missing_value (10st) | PASS verified_done (2st) |
| github-repo-search | fail call_budget (2st) | PASS done (1st) | fail call_budget (4st) | PASS verified_done (1st) |
| reddit-search | fail call_budget (9st) | fail call_budget (1st) | PASS call_budget (7st) | fail denied (1st) |
| practice-form-fill | PASS bench_missing_value (3st) | PASS loop (7st) | fail bench_missing_value (1st) | PASS call_budget (3st) |
| datepicker-set-date | fail step_limit (60st) | fail step_limit (60st) | fail step_limit (60st) | fail step_limit (60st) |
| stackoverflow-search | fail step_limit (60st) | fail step_limit (60st) | fail step_limit (60st) | fail step_limit (60st) |
| imdb-title-lookup | fail done (7st) | PASS verified_done (1st) | PASS done (2st) | fail done (26st) |
| currency-convert-usd-eur | fail done (1st) | fail done (3st) | fail done (2st) | fail done (5st) |
| dictionary-definition | PASS done (4st) | PASS verified_done (1st) | PASS done (4st) | PASS verified_done (1st) |
| **total** | 4/18 | 9/18 | 6/18 | 6/18 |

Planner went from 9/18 to 6/18. The drop comes from bot walls and the agent wandering off course, not from the new goal-value check.

## Planner regressions vs 0e17de7 (fleet scout analysis, checked against results.jsonl)
- amazon: bot wall. It ended on `/checkpoint/continue` ("I am human"). Correctly rejected: "search results page for 'usb-c cable' not found - currently on human verification page".
- ebay: site error page ("SORRY Something went wrong on our end"). Correctly rejected: "Error page shown instead of search results".
- gmaps: the agent wandered. After 28 steps it hit call_budget on `/maps/dir///@51.36,12.39` (Leipzig). Its last action was "Start und Ziel vertauschen". No Louvre on the page.
- imdb: the agent wandered to `/interest/the-matrix/` (a keyword page, not the title page). **The verifier accepted it (verified_done=true).**

## Goal-value check
- **No correct page was rejected.** Every planner rejection was legitimate: the amazon checkpoint, the eBay error page, YouTube results instead of a video (missing: "first video result not clicked, not on video watch page"), and the stop-code failures.
- **New false positives from the end-of-run verifier (verified_done=true, stop code done):**
  - xe: `/currencyconverter/convert/?Amount=100&From=EUR&To=INR`, showing "100 EUR to INR". The goal is 100 USD to EUR, so the verifier accepted the wrong currency pair. The scout called this a correct rejection; results.jsonl says it was accepted.
  - imdb: the `/interest/the-matrix/` keyword page was accepted as the film's title page.

## Date picker
- **It does not type the date.** Planner run: 60 steps, ending on `demoqa.com/` with last_action "link". Agent run: 60 steps, ending on `demoqa.com/forms`. Neither final page contains "03/15/2027", and both runs left the date-picker page.

## Other notes
- The agent-mode practice-form fail is a missing_value stop ('First Name'), the same artifact as before. The planner-mode "pass" (call_budget, 3 steps) is doubtful: the fields look empty in the page text, and the check matched on the action list.
- Verified correct pages: wikipedia, weather (the London tenday page now passes the fixed check), allrecipes, github, dictionary.

## Top causes (5e559ac, both modes)
1. step_limit (8): "Reached the 60-action limit without finishing the goal." (booking, flights, date picker, stackoverflow in both modes)
2. call_budget (5): "Stopped after 120 model decisions without finishing (limit 120). The agent was probably going in circles." (gmaps in both modes, github agent, reddit agent, practice-form planner)
3. Bot walls / site errors (5): the amazon checkpoint in both modes, the eBay error page in both modes, BBC pushed to accounts.google.com.
4. False DONE (5): "The agent decided the goal is complete." (xe in both modes, imdb planner, youtube planner on the results page, amazon agent on the checkpoint)
