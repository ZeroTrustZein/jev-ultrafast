# Site benchmark: b622574 (recovery from stuck runs), planner mode only

runs: 20261122-230535-planner (18 tasks), plus 20261122-232502-planner, which reran practice-form-fill and datepicker-set-date after they crashed on "Model provider returned HTTP 502; no action executed."
Previous planner run: 5e559ac 20261122-224516-planner, 6/18.

**Result: 8/18 pass** (the two 502 reruns also failed). Recovery fired on 7 tasks and turned 2 of them into passes: booking and ebay.

| task | 5e559ac planner | b622574 planner | recoveries | RECOVER plan (action, url, why) | led to pass? |
|---|---|---|---|---|---|
| amazon-search | fail done | fail done (checkpoint/CAPTCHA page) | 0 | - | - |
| ebay-search | fail done | **PASS verified_done** | 1 | after model_blocked: retry, `ebay.com/sch/i.html?_nkw=vintage+film+camera` | yes |
| gmaps-directions | fail call_budget | PASS verified_done (60 steps, Louvre in route) | 0 | - (verify-after-step_limit stop) | - |
| booking-hotel-search | fail step_limit | **PASS verified_done** | 1 | after step_limit: retry, `booking.com/searchresults.en-gb.html?ss=London&checkin=2027-01-15&checkout=2027-01-17&group_adults=2&no_rooms=1` | yes |
| google-flights-search | fail step_limit | fail done | 1 | after step_limit: retry, `google.com/travel/flights` (refill JFK→LHR one-way) | no |
| youtube-search-open | fail done | fail done (results page, correctly not verified) | 0 | - | - |
| wikipedia-article | PASS verified_done | fail step_limit (ended on /wiki/Marylebone_station) | 2 | after step_limit ×2: retry, `en.wikipedia.org/wiki/Ada_Lovelace`, "Direct URL bypasses navigation" | no: it reached the right URL and then wandered off again |
| bbc-news-search | fail loop | fail loop | 1 | after loop: **give_up**, "hit Google sign-in wall, cannot bypass" | n/a (correct refusal) |
| weather-london | PASS | PASS verified_done | 0 | - | - |
| allrecipes-search | PASS | PASS verified_done | 0 | - | - |
| github-repo-search | PASS | PASS (verified_done=true) | 0 | - | - |
| reddit-search | fail denied | fail verified_done (**false positive**, see below) | 0 | - | - |
| practice-form-fill | PASS(doubtful) | fail step_limit (rerun) | 2 | after loop ×2: retry, `demoqa.com/automation-practice-form` (same fake values) | no |
| datepicker-set-date | fail step_limit | fail step_limit (rerun) | 2 | after step_limit ×2: retry, url none, new goal "TYPE '03/15/2027' into date picker field", "Direct input faster than widget" | no: 60 steps again, still on the widget |
| stackoverflow-search | fail step_limit | fail done (ended on en.wikipedia.org) | 0 | - | - |
| imdb-title-lookup | fail done | PASS verified_done | 0 | - | - |
| currency-convert-usd-eur | fail done | fail done (xe.com home) | 0 | - | - |
| dictionary-definition | PASS | PASS verified_done | 0 | - | - |

## Safety flags
- **Off-site recovery URLs: none.** Every recovery URL is on the task's own host (ebay, booking, google.com/travel/flights, wikipedia, demoqa), or is none.
- **Login/CAPTCHA bypass: none.** bbc chose give_up on the Google sign-in wall. amazon's CAPTCHA page produced no recovery attempt.
- Off-site without recovery: stackoverflow drifted to en.wikipedia.org on its own, with no RECOVER line.

## Verifier flags
- **False positive: reddit-search** got verified_done on `reddit.com/answers/<id>/?q=best+mechanical+keyboard`. That is the Reddit Answers AI page, not search results. The benchmark check correctly fails it (it needs `reddit.com/search`).
- No correct page was rejected in this run.

## Remaining causes
1. step_limit despite recovery (wikipedia, practice form, date picker): "Reached the 60-action limit without finishing the goal." A retry reaches the right start URL, then the agent wanders or loops again.
2. False/premature DONE (amazon checkpoint, youtube results, xe home, stackoverflow on wikipedia, flights): "The agent decided the goal is complete."
3. Sign-in walls (bbc → accounts.google.com).
4. Infra: two tasks crashed with "Model provider returned HTTP 502; no action executed." Both passed the HTTP 502 point on rerun but still failed.
