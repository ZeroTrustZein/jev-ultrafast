"""Step 4: when a run gets stuck, plan another route instead of quitting.

The fast loop stays as it is. A recovery runs only after the agent has stopped on a recoverable reason, costs one
planner call, and starts a fresh attempt in the same tab with a sharper goal and, optionally, a better start URL.
"""

import json
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
- Login, CAPTCHA or bot-check pages cannot be bypassed: choose ask_user (if the user can help, e.g. by signing in
  themselves) or give_up. Never try to defeat a CAPTCHA.
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
