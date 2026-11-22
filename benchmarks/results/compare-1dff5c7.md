# Site benchmark: baseline (main) vs 87089a7 vs 1dff5c7

runs: baseline 20261122-204642 | 87089a7 20261122-210521-agent, 20261122-211510-planner | 1dff5c7 20261122-213150-agent, 20261122-214129-planner

| task | baseline (agent) | 87089a7 agent | 87089a7 planner | 1dff5c7 agent | 1dff5c7 planner |
|---|---|---|---|---|---|
| amazon-search | fail model_blocked (0st) | PASS done (21st) | PASS done (5st) | fail done (2st) | fail done (2st) |
| ebay-search | fail model_blocked (0st) | fail done (46st) | fail done (2st) | fail done (7st) | fail done (4st) |
| gmaps-directions | fail loop (5st) | PASS loop (8st) | PASS loop (6st) | PASS call_budget (56st) | PASS call_budget (55st) |
| booking-hotel-search | fail model_blocked (0st) | fail denied (0st) | fail step_limit (60st) | fail step_limit (60st) | PASS done (3st) |
| google-flights-search | fail bench_missing_value (4st) | fail bench_missing_value (4st) | fail denied (10st) | fail step_limit (60st) | fail step_limit (60st) |
| youtube-search-open | fail model_blocked (0st) | fail done (3st) | PASS step_limit (60st) | fail done (3st) | fail done (4st) |
| wikipedia-article | PASS done (2st) | PASS done (2st) | fail step_limit (60st) | PASS done (2st) | fail step_limit (60st) |
| bbc-news-search | fail model_blocked (2st) | fail bench_missing_value (2st) | fail loop (9st) | fail bench_missing_value (3st) | fail loop (11st) |
| weather-london | fail done (4st) | PASS done (1st) | PASS denied (3st) | PASS done (3st) | PASS verified_done (1st) |
| allrecipes-search | fail done (8st) | PASS loop (6st) | fail done (11st) | fail done (10st) | PASS verified_done (1st) |
| github-repo-search | fail bench_timeout (Nonest) | fail call_budget (2st) | fail done (1st) | fail call_budget (2st) | PASS done (2st) |
| reddit-search | fail loop (6st) | fail loop (6st) | PASS done (2st) | fail loop (6st) | PASS call_budget (0st) |
| practice-form-fill | PASS bench_missing_value (5st) | PASS bench_missing_value (5st) | PASS loop (7st) | PASS bench_missing_value (5st) | PASS loop (7st) |
| datepicker-set-date | fail loop (3st) | fail loop (3st) | fail loop (3st) | fail loop (3st) | fail loop (3st) |
| stackoverflow-search | fail model_blocked (0st) | fail loop (10st) | fail loop (4st) | PASS loop (20st) | fail loop (15st) |
| imdb-title-lookup | fail model_blocked (0st) | fail denied (1st) | PASS done (1st) | fail done (2st) | PASS done (3st) |
| currency-convert-usd-eur | fail done (1st) | fail done (1st) | fail done (5st) | fail done (1st) | fail done (32st) |
| dictionary-definition | fail call_budget (0st) | PASS done (4st) | PASS step_limit (60st) | PASS done (4st) | PASS verified_done (1st) |
| **total** | 2/18 | 7/18 | 8/18 | 6/18 | 9/18 |

## 1dff5c7 failures and verifier checks
- agent amazon-search: done | The agent decided the goal is complete. | https://www.amazon.com/s?k=usb-c%20cable&i=aps&ref=nb_sb_ss_p13n-expert-pd-ops-ranker_ci_h
- agent ebay-search: done | The agent decided the goal is complete. | https://www.ebay.com/sch/i.html?_nkw=vintage+film+camera&_sacat=0&_from=R40&_trksid=p23345
- agent gmaps-directions: call_budget | Stopped after 120 model decisions without finishing (limit 120). The agent was probably going in circles. | https://www.google.com/maps/dir/Eiffelturm,+Av.+Gustave+Eiffel,+75007+Paris,+Frankreich/Lo <<PASS WITHOUT CLEAN STOP
- agent booking-hotel-search: step_limit | Reached the 60-action limit without finishing the goal. | https://www.booking.com/
- agent google-flights-search: step_limit | Reached the 60-action limit without finishing the goal. | https://www.google.com/travel/flights?tfs=CBwQARojEgoyMDI3LTAxLTE1agcIARIDTEhScgwIAhIIL20v
- agent youtube-search-open: done | The agent decided the goal is complete. | https://www.youtube.com/results?search_query=python+tutorial+for+beginners
- agent bbc-news-search: bench_missing_value | form asked for 'E-Mail oder Telefonnummer' (benchmark gives no profile) | https://accounts.google.com/v3/signin/identifier?continue=https://www.google.com/preferenc
- agent allrecipes-search: done | The agent decided the goal is complete. | https://www.allrecipes.com/recipes-a-z-6735880
- agent github-repo-search: call_budget | Stopped after 120 model decisions without finishing (limit 120). The agent was probably going in circles. | https://github.com/
- agent reddit-search: loop | Stuck: the last 3 actions did not change the page, even with the vision fallback. Repeated: Open leadingIcon chips  header               trailingContent. | https://www.reddit.com/posts/2026/global/
- agent practice-form-fill: bench_missing_value | form asked for 'First Name' (benchmark gives no profile) | https://demoqa.com/automation-practice-form <<PASS WITHOUT CLEAN STOP
- agent datepicker-set-date: loop | Stuck: the last 3 actions did not change the page, even with the vision fallback. Repeated: Date Picker. | https://demoqa.com/date-picker
- agent stackoverflow-search: loop | Stuck: the last 3 actions did not change the page, even with the vision fallback. Repeated: Popular. | https://www.cloudflare.com/domains/search?utm_source=challenge&utm_campaign=j&q=python+lis <<PASS WITHOUT CLEAN STOP
- agent imdb-title-lookup: done | The agent decided the goal is complete. | https://www.imdb.com/title/tt0133093/?ref_=nv_sr_srsg_0_tt_8_nm_0_in_0_q_The%20Matrix%2019
- agent currency-convert-usd-eur: done | The agent decided the goal is complete. | https://www.xe.com/
- planner amazon-search: done | The agent decided the goal is complete. | https://www.amazon.com/checkpoint/continue?token=v1.aU37q4LMuzWY6Kd-hhiIwwLzEzELW0FlfwTUbb
- planner ebay-search: done | The agent decided the goal is complete. | https://www.ebay.com/sch/i.html?_nkw=vintage+film+camera&_sacat=0&_from=R40&_trksid=m570.l
- planner gmaps-directions: call_budget | Stopped after 120 model decisions without finishing (limit 120). The agent was probably going in circles. | https://www.google.com/maps/dir/Eiffelturm,+Av.+Gustave+Eiffel,+75007+Paris,+Frankreich/Lo <<PASS WITHOUT CLEAN STOP
- planner google-flights-search: step_limit | Reached the 60-action limit without finishing the goal. | https://www.google.com/travel/flights?tfs=CBwQARoJagcIARIDSkZLQAFIAXABggELCP___________wGY
- planner youtube-search-open: done | The agent decided the goal is complete. | https://www.youtube.com/results?search_query=python+tutorial+for+beginners
- planner wikipedia-article: step_limit | Reached the 60-action limit without finishing the goal. | https://www.themarginalian.org/index.php/2013/12/10/ada-lovelace-science-religion-letter/
- planner bbc-news-search: loop | Stuck: the last 3 actions did not change the page, even with the vision fallback. Repeated: Vorname. | https://accounts.google.com/signin/v2/queryname?continue=https%3A%2F%2Fwww.google.com%2Fpr
- planner reddit-search: call_budget | Stopped after 120 model decisions without finishing (limit 120). The agent was probably going in circles. | https://www.reddit.com/search/?q=best%20mechanical%20keyboard <<PASS WITHOUT CLEAN STOP
- planner practice-form-fill: loop | Stuck: the last 3 actions did not change the page, even with the vision fallback. Repeated: Current Address, Open First Name. | https://demoqa.com/automation-practice-form <<PASS WITHOUT CLEAN STOP
- planner datepicker-set-date: loop | Stuck: the last 3 actions did not change the page, even with the vision fallback. Repeated: Date Picker. | https://demoqa.com/date-picker
- planner stackoverflow-search: loop | Stuck: the last 3 actions did not change the page, even with the vision fallback. Repeated: Search Wikipedia. | https://en.wikipedia.org/wiki/Python_list_comprehension
- planner currency-convert-usd-eur: done | The agent decided the goal is complete. | https://www.xe.com/currencyconverter/

## Verifier check (1dff5c7 planner run, checked against final page text)
- **False positive:** weather-london got verified_done on `weather.com/?Goto=Redirected`. That is the home page geolocated to "Berlin-Mitte Weather"; London only appears as a recents chip ("London / Fair / 42°"). The benchmark check passes it too (it only needs "London" in the text), so the check is still too loose.
- **False negative:** github-repo-search ended on the right page (`github.com/search?q=playwright&type=repositories`, "repositories Search Results · playwright") and was rejected with "Verifier evidence is not on the page."
- **Correct page, but no verified_done stop:** gmaps-directions (route shown, then call_budget) and reddit-search (`/search/?q=best mechanical keyboard`, then call_budget). The per-URL goal check never fired, and the agent kept going until it ran out of model decisions.
- Correct rejections: amazon (checkpoint page), ebay (bot error page), youtube (results page, not /watch), xe (no conversion shown).

## Benchmark artifacts in 1dff5c7 agent mode
- amazon and imdb agent runs ended on the correct URL (`/s?k=usb-c cable`, `/title/tt0133093`) but the final page text was empty, so the check failed. Counting those, agent mode is really 8/18.
- stackoverflow agent "PASS" is false: the run ended on cloudflare.com/domains/search, reached from the bot challenge.

## Remaining top causes (1dff5c7, both modes)
1. False/premature DONE (10). "The agent decided the goal is complete." Cases: eBay bot page, xe with no conversion, YouTube results instead of a video, allrecipes A-Z page.
2. Loops (7). "Stuck: the last 3 actions did not change the page, even with the vision fallback. Repeated: Date Picker." (date picker in both modes, reddit, bbc sign-in, stackoverflow)
3. step_limit (4). "Reached the 60-action limit without finishing the goal." (google flights in both modes, booking agent, wikipedia planner off-site to themarginalian.org)
4. call_budget on pages that already meet the goal (4). "Stopped after 120 model decisions without finishing (limit 120)." (gmaps in both modes, reddit planner, github agent)
5. Bot and sign-in walls: amazon checkpoint, eBay error page, Cloudflare on stackoverflow, BBC pushed into accounts.google.com ("form asked for 'E-Mail oder Telefonnummer'").
