"""Step 4: when a run gets stuck, plan another route instead of quitting.

The fast loop stays as it is. A recovery runs only after the agent has stopped on a recoverable reason, costs one
planner call, and starts a fresh attempt in the same tab with a sharper goal and, optionally, a better start URL.
"""

import json
import time
from urllib.parse import urlparse

# Stops where another route may work. Refusals (a declined click), crashes and successes are never retried.
RECOVERABLE = {"loop", "call_budget", "step_limit", "idle", "model_blocked", "empty_page", "missing_target"}
MAX_RECOVERIES = 2

RECOVER = """A fast browser agent got stuck on a task. Plan ONE different route for a fresh attempt.
Return JSON: {"action": "retry"|"ask_user"|"give_up", "url": "optional new start URL or empty",
"goal": "the goal rewritten for the new route", "question": "only for ask_user", "why": "one short sentence"}.
- Do not repeat what failed (see attempts). Avoid the widget or link that looped.
- Prefer a direct URL that skips the broken step: a site's own search URL with the query in it
  (e.g. https://www.reddit.com/search/?q=..., https://www.youtube.com/results?search_query=...), or the
  exact page. Use only URLs on the task's site or an official one; never invent personal data.
- For date pickers and similar widgets, tell the agent to TYPE the value into the field.
- If the current page is a directory or comparison entry (a programme database, a price-comparison listing) and the
  missing facts (application steps, deadlines, delivery, full specs) belong to the provider, do not go back to the
  directory's search: retry with an empty url and a goal to click the provider's own website or offer link on the
  current page, then read the facts there.
- Login, CAPTCHA or bot-check pages cannot be bypassed; never try to defeat a CAPTCHA. After a bot check nobody
  cleared (stop bot_check), retry on a different site that offers the same information (another shop, a
  price-comparison site, the official portal, or https://html.duckduckgo.com/html/?q=...), never the walled site.
  For a login only the user can do, choose ask_user or give_up.
- Keep every requirement and stopping rule of the original goal, including "do not submit/sign in/pay"."""


def plan_recovery(original_goal, agent, ask=None):
    """One planner call. Returns a dict with action retry|ask_user|give_up; anything malformed means give_up."""
    if ask is None:
        from .planner import ask
    state = agent.state
    page = state.get("page") or {}
    stop = state.get("stop_reason") or {}
    tried = [h.get("action") for h in state.get("history", [])[-10:]]
    content = json.dumps(
        {
            "original_goal": original_goal,
            "today": time.strftime("%Y-%m-%d"),
            "stop": {"code": stop.get("code"), "message": stop.get("message")},
            "current_url": page.get("url"),
            "current_title": page.get("title"),
            "page_text": str(page.get("text") or "")[:4000],
            "last_actions": tried,
            "earlier_attempts": state.get("attempts", []),
        }
    )
    try:
        plan = ask(RECOVER, content)
    except Exception as exc:
        return {"action": "give_up", "why": f"Recovery planner failed: {exc}"}
    if not isinstance(plan, dict) or plan.get("action") not in {"retry", "ask_user", "give_up"}:
        return {"action": "give_up", "why": "Recovery planner returned no usable plan."}
    url = str(plan.get("url") or "").strip()
    plan["url"] = url if urlparse(url).scheme in {"http", "https"} else None
    route = str(plan.get("goal") or "").strip()
    # The original goal always stays in force; the route only adds how to get there this time.
    plan["goal"] = f"{original_goal}\nNew route after getting stuck ({stop.get('code')}): {route}" if route else None
    if plan["action"] == "retry" and not (plan["goal"] or plan["url"]):
        return {"action": "give_up", "why": "Recovery planner offered no new route."}
    plan["goal"] = plan["goal"] or original_goal
    return plan
