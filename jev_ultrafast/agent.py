"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from .browser import Browser, StalePage, bot_wall, is_empty
from .model import MissingValue, action_space, choose, choose_vision, choose_vision_jev, field_context, field_text
from .questions import MAX_STEPS

# Irreversible or outward-facing clicks. Code-owned, so a model cannot talk its way past the gate.
SENSITIVE = re.compile(r"\b(submit|pay|purchase|buy|order|book|confirm|send|sign|apply|delete|checkout)\b", re.I)
# Always gated, whatever else the label says ("Cancel order" still cancels an order).
IRREVERSIBLE = re.compile(r"\b(pay|purchase|buy|order|book|confirm|send|delete|checkout)\b", re.I)
# Soft words (submit, sign, apply) next to these are harmless: "Submit search", "Dismiss sign-in info".
HARMLESS = re.compile(
    r"\b(search|dismiss|close|cancel|no thanks|not now|filters?|sort|skip|later|price|range|refine|results)\b", re.I
)

# Waits on a human-verification page before asking the person (Cloudflare usually clears within ~5 s).
BOT_WALL_WAITS = 3
BOT_WALL_PAUSE = 2.0
# Consecutive actions lost to a page that changed first; then the run stops as a loop so recovery starts early.
STALE_LIMIT = 6
# Controls that are meant to be pressed again and again on one URL ("Load more", "Next", "+").
REPEATABLE = re.compile(r"\b(more|next|previous|prev|load|increase|decrease|add|plus|minus)\b|^[+\-]$", re.I)


def _route(url):
    parts = urlparse(url or "")
    return parts.netloc, parts.path.rstrip("/")


def sensitive(label, role=None):
    # Judge the caption a person would read, not page code that leaked into it ("button[type=submit]", "{order:2}").
    label = " ".join(str(label or "").split())[:200]
    if IRREVERSIBLE.search(label):
        return True
    # A link only navigates; soft words there are topics ("How to apply?", "Sign language"), not form submissions.
    if role == "link":
        return False
    return bool(SENSITIVE.search(label)) and not HARMLESS.search(label)


def gear2(page, goal, history, screenshot, reason):
    """GEAR2_BACKEND: jev (TypeSafe vision, default) | vlm (OmniRoute VLM) | laya (local Jev-compatible server)."""
    backend = os.environ.get("GEAR2_BACKEND", "jev")
    if backend == "vlm":
        return choose_vision(page, goal, history, screenshot, reason=reason)
    base = os.environ.get("GEAR2_LAYA_URL", "http://127.0.0.1:8791") if backend == "laya" else None
    decision = choose_vision_jev(page, goal, history, screenshot, reason=reason, base=base)
    # Benchmarks: when both gears are the same small model, an unsure gear 1 is followed by an equally unsure gear 2,
    # which then acts almost at random (YouTube never opened a video). With a vision LLM configured (GEAR2_MODEL),
    # hand the unsure or stuck cases to it instead.
    threshold = float(os.environ.get("GEAR2_CONFIDENCE_THRESHOLD", "0.65"))
    unsure = decision.get("confidence", 1.0) < threshold or decision.get("choice") in {"BLOCKED", "DONE"}
    if os.environ.get("GEAR2_MODEL") and (unsure or reason.startswith("looping")):
        try:
            better = choose_vision(page, goal, history, screenshot, reason=f"{reason}; small vision model unsure")
            return {**better, "escalated_from": decision.get("model")}
        except Exception as exc:
            decision["gear2_error"] = f"vision LLM failed: {type(exc).__name__}: {exc}"
    return decision


class Agent:
    profile = approve = None
    files = ()

    def __init__(self, url, goals, *, record_dir=None, screenshots=False, profile=None, approve=None, files=()):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text = None
        self.profile = profile
        self.approve = approve
        self.files = [str(Path(f).resolve()) for f in files]
        self.browser = Browser(url)
        self.browser.allow_upload = bool(self.files)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            elapsed_ms=0,
            started_at=None,
            stop_reason=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def restart(self, goal, url=None):
        """Recovery: a fresh attempt in the same tab, with a new goal and optionally a new start URL.

        The stuck attempt is kept in state["attempts"] so the next recovery plan can see what already failed.
        History and the decision budget start over, so the new route gets its full step and call budget.
        """
        state = self.state
        state.setdefault("attempts", []).append(
            {
                "stop": (state.get("stop_reason") or {}).get("code"),
                "message": (state.get("stop_reason") or {}).get("message"),
                "url": (state.get("page") or {}).get("url"),
                "steps": len(state["history"]),
                "last_actions": [h.get("action") for h in state["history"][-8:]],
            }
        )
        if url:
            state["browser"].call("Page.navigate", url=url)
            state["browser"].wait_ready()
        state["page"] = state["browser"].observe(screenshot=self.screenshots)
        state.update(goal=goal, history=[], decisions=[], decision=None, status="ready", stop_reason=None)
        state.pop("denied", None)
        state.pop("stale_action", None)
        state["stale_streak"] = 0
        self.pending_text = None
        return self.snapshot()

    def without_dead_ends(self, page):
        """The page as the models see it, minus targets that already did nothing twice on this URL.

        Benchmarks showed loops on widgets (date pickers, nav chips, autocompletes): the same click, no page change,
        again and again. Hiding a proven no-op forces a different route, such as typing into the field instead.
        """
        counts = {}
        for h in self.state["history"]:
            if h.get("page_changed") is False and h.get("kind") not in {"wait", "fill"} and h.get("url") == page["url"]:
                counts[h["action"]] = counts.get(h["action"], 0) + 1
        dead = {label for label, n in counts.items() if n >= 2}
        # Toggles: a filter chip or tab flips the page each time, so it never looks like a no-op, yet the third click
        # of the same control on the same URL is undoing earlier work (Google Flights filters, again and again).
        # Compared by host and path: filters often rewrite the query string (?tfs=..., ?sort=...).
        here = _route(page["url"])
        toggles = {}
        for h in self.state["history"][-12:]:
            if h.get("kind") in {"click", "select"} and _route(h.get("url")) == here:
                toggles[h["action"]] = toggles.get(h["action"], 0) + 1
        dead |= {label for label, n in toggles.items() if n >= 3 and not REPEATABLE.search(label or "")}
        # Ping-pong: clicks that change the page but only bounce between two URLs (demoqa: logo -> home, Forms ->
        # forms, again and again). Those pages change, so the no-change rule above never sees it.
        recent = self.state["history"][-6:]
        if len(recent) == 6 and len({h.get("url") for h in recent}) <= 2:
            repeats = {}
            for h in recent:
                if h.get("kind") not in {"wait", "fill"}:
                    repeats[h["action"]] = repeats.get(h["action"], 0) + 1
            dead |= {label for label, n in repeats.items() if n >= 2}
        if not dead:
            return page
        kept = [a for a in page["actions"] if a.get("kind") == "wait" or a.get("label") not in dead]
        if not any(a.get("kind") != "wait" for a in kept):
            return page  # Never hide everything; let the stop rules decide instead.
        return {**page, "actions": kept, "dead_ends": sorted(dead)}

    def stop(self, status, code, message, **details):
        """End the run with a reason a person can read. Every stop goes through here, so none is silent."""
        state = self.state
        page = state.get("page") or {}
        last = state["history"][-1] if state["history"] else {}
        state["status"] = status
        if state.get("started_at") is not None:
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
        state["stop_reason"] = {
            "code": code,
            "message": message,
            "steps": len(state["history"]),
            "url": page.get("url"),
            "title": page.get("title"),
            "last_action": last.get("action"),
            **details,
        }
        path = os.environ.get("JEV_STOP_LOG")
        if path:
            try:
                with open(path, "a", encoding="utf-8") as log:
                    log.write(json.dumps({"goal": state["goal"], "status": status, **state["stop_reason"]}) + "\n")
            except OSError:
                pass
        return self.snapshot()

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                if state["status"] in {"done", "blocked"}:
                    return self.snapshot()
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                # Ads, carousels or hidden inputs can change the page between every decision and its action. Re-asking
                # the models each time burned the whole call budget on one home page (merriam-webster: 120 decisions,
                # 0 actions). Re-use the decision on the fresh page instead, and stop early if it never settles.
                state["stale_streak"] = state.get("stale_streak", 0) + 1
                if state["stale_streak"] >= STALE_LIMIT:
                    state.pop("stale_action", None)
                    return self.stop(
                        "blocked",
                        "loop",
                        f"The page changed before each of the last {STALE_LIMIT} actions (ads or animation), "
                        "so no action could be taken safely.",
                    )
                state["decision"] = None
                state["status"] = "ready"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                return self.stop(
                    "blocked",
                    "call_budget",
                    f"Stopped after {len(state['decisions'])} model decisions without finishing "
                    f"(limit {MAX_STEPS * 2}). The agent was probably going in circles.",
                )
            gear2_on = os.environ.get("GEAR2", "1") == "1"
            if os.environ.get("JEV_LOG") and not state["page"].get("screenshot"):
                # Teacher logs pair each decision with its pixels; no capture cost when not logging.
                try:
                    shot = state["browser"].call("Page.captureScreenshot", format="jpeg", quality=60)
                    state["page"]["screenshot"] = shot.get("data")
                except Exception:
                    pass
            wall = bot_wall(state["page"])
            if wall:
                waited = 0
                for h in reversed(state["history"]):
                    if not h.get("wall"):
                        break
                    waited += 1
                if waited >= BOT_WALL_WAITS or not any(a["id"] == "wait" for a in state["page"]["actions"]):
                    host = urlparse(state["page"].get("url") or "").netloc or "This site"
                    return self.stop(
                        "blocked",
                        "bot_check",
                        f"{host} is showing a human-verification check (\"{wall}\"). I don't solve those. "
                        "Please complete it in the browser, then tell me to continue.",
                    )
                # Some checks clear on their own after a few seconds. Wait, and never touch the check itself.
                state["decision"] = {
                    "choice": "wait",
                    "operation": "WAIT",
                    "target": None,
                    "confidence": 1.0,
                    "probabilities": {"wait": 1.0},
                    "latency_ms": 0,
                    "usage": {},
                    "gear": 0,
                    "wall": wall,
                    "reason": "human-verification page; waiting to see if it clears",
                }
                gear2_on = False
            elif is_empty(state["page"]) and any(a["id"] == "wait" for a in state["page"]["actions"]):
                # Nothing rendered yet. Asking the models here only produced BLOCKED; wait instead.
                state["decision"] = {
                    "choice": "wait",
                    "operation": "WAIT",
                    "target": None,
                    "confidence": 1.0,
                    "probabilities": {"wait": 1.0},
                    "latency_ms": 0,
                    "usage": {},
                    "gear": 0,
                    "reason": "page has not rendered yet",
                }
                gear2_on = False
            else:
                state["decision"] = None
            stale = state.pop("stale_action", None)
            if state["decision"] is None and stale:
                same = (stale["kind"], stale["label"])
                again = next((a for a in state["page"]["actions"] if (a["kind"], a["label"]) == same), None)
                if again:
                    state["decision"] = {
                        **stale["decision"],
                        "choice": again["id"],
                        "probabilities": {again["id"]: 1.0},
                        "latency_ms": 0,
                        "usage": {},
                        "reason": "same choice, re-found on the changed page",
                    }
                    gear2_on = False
            try:
                if state["decision"] is None:
                    state["decision"] = choose(self.without_dead_ends(state["page"]), state["goal"], state["history"])
            except Exception as exc:
                if gear2_on:
                    screenshot = state["browser"].call("Page.captureScreenshot", format="jpeg", quality=60)["data"]
                    state["decision"] = gear2(
                        self.without_dead_ends(state["page"]),
                        state["goal"],
                        state["history"],
                        screenshot,
                        reason=f"gear 1 error: {exc}",
                    )
                else:
                    raise

            # 2-Gear Hybrid Switch: Wake up Gear 2 (Multimodal VLM) if Gear 1 is low-confidence, blocked, looping, or premature DONE
            low_conf = state["decision"].get("confidence", 1.0) < float(
                os.environ.get("GEAR2_CONFIDENCE_THRESHOLD", "0.65")
            )
            is_blocked = state["decision"].get("choice") == "BLOCKED"
            is_looping = len(state["history"]) >= 2 and all(
                h.get("page_changed") is False and h.get("kind") != "wait" for h in state["history"][-2:]
            )
            has_action_goal = any(w in state["goal"].lower() for w in ("click", "select", "press", "open", "type", "fill", "register", "download", "remove", "add", "pick"))
            premature_done = state["decision"].get("choice") == "DONE" and len(state["history"]) == 0 and has_action_goal
            if gear2_on and state["decision"].get("gear") != 2 and (low_conf or is_blocked or is_looping or premature_done):
                trigger_reason = (
                    "premature DONE before any action"
                    if premature_done
                    else ("looping with no change" if is_looping else ("low confidence" if low_conf else "gear 1 chose BLOCKED"))
                )
                try:
                    screenshot = state["browser"].call("Page.captureScreenshot", format="jpeg", quality=60)["data"]
                    state["decision"] = gear2(
                        self.without_dead_ends(state["page"]),
                        state["goal"],
                        state["history"],
                        screenshot,
                        reason=trigger_reason,
                    )
                except Exception as exc:
                    state["decision"]["gear"] = 1
                    state["decision"]["gear2_error"] = f"{type(exc).__name__}: {exc}"
            elif "gear" not in state["decision"]:
                state["decision"]["gear"] = 1

            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                state["plan_index"] = int(selected == "DONE")
                gear = decision.get("gear", 1)
                said = (decision.get("reason") or "").strip()
                if selected == "DONE":
                    message = "The agent decided the goal is complete."
                    return self.stop("done", "done", message, gear=gear, model_reason=said)
                who = "Both the fast model and the vision fallback" if gear == 2 else "The fast model"
                message = f"{who} found no action that makes progress on this page."
                if gear == 1 and os.environ.get("GEAR2", "1") != "1":
                    message += " The vision fallback is switched off (GEAR2=0)."
                if decision.get("gear2_error"):
                    message += f" The vision fallback failed: {decision['gear2_error']}"
                return self.stop("blocked", "model_blocked", message, gear=gear, model_reason=said)
            if selected == "CLICK_COORDS":
                action = {
                    "id": "coords",
                    "kind": "coords",
                    "label": f"Click ({decision.get('x')}, {decision.get('y')})",
                    "x": int(decision["x"]),
                    "y": int(decision["y"]),
                }
            else:
                action = next((a for a in page["actions"] if str(a["id"]).lower() == str(selected).lower()), None)
                if not action:
                    action = next((a for a in page["actions"] if a["id"] == "wait"), None)
                    if not action:
                        return self.stop(
                            "blocked", "missing_target", f"The model picked '{selected}', which is not on the page."
                        )
            if len(state["history"]) >= MAX_STEPS:
                return self.stop(
                    "blocked", "step_limit", f"Reached the {MAX_STEPS}-action limit without finishing the goal."
                )
            # Kept until the action runs: if the page changes first, the next tick re-uses this choice.
            if action["kind"] in {"click", "select", "fill", "upload"}:
                state["stale_action"] = {"kind": action["kind"], "label": action["label"], "decision": decision}
            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                from .model import PERSONAL_FIELD

                if (
                    decision.get("gear") == 2
                    and decision.get("text")
                    and not PERSONAL_FIELD.search(action.get("label", ""))
                ):
                    text = decision["text"]
                    helper = {"model": decision["model"], "latency_ms": 0, "usage": {}}
                else:
                    context = field_context(state["goal"], action, page, state["history"], self.profile)
                    if self.pending_text and self.pending_text[0] == context:
                        _, text, helper = self.pending_text
                    else:
                        text, helper = field_text(context)
                        self.pending_text = (context, text, helper)
                        state["text_calls"].append({**helper, "field": action["label"], "value": text})
            if action["kind"] == "upload":
                text, helper = self.pick_file(action, page)
                state["text_calls"].append({**helper, "field": action["label"], "value": Path(text).name})
            # Ticking a checkbox/radio only changes form state; the irreversible step is the button that sends it.
            gated = action.get("role") not in {"checkbox", "radio", "switch"}
            if (
                self.approve
                and gated
                and action["kind"] in {"click", "select", "coords"}
                and sensitive(action["label"], action.get("role"))
            ):
                if not self.approve(action):
                    state["denied"] = action["label"]
                    return self.stop(
                        "blocked", "denied", f"You did not approve the sensitive click '{action['label']}'."
                    )
            if decision.get("wall"):
                time.sleep(BOT_WALL_PAUSE)
            # Browser.act checks freshness immediately before input, including after text generation.
            # After two lost tries on a churning page, a click only needs its own target unchanged (see Browser.fresh).
            loose = state.get("stale_streak", 0) >= 2 and action["kind"] in {"click", "coords"}
            state["browser"].act(action, page, text=text, loose=loose)
            state.pop("stale_action", None)
            state["stale_streak"] = 0
            self.pending_text = None
            if action["kind"] in {"click", "select", "coords"}:
                time.sleep(0.15)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision.get("target"),
                    "gear": decision.get("gear", 1),
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "wall": decision.get("wall"),
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            gear2_tried = any(h.get("gear") == 2 for h in repeated)
            stuck = (
                len(repeated) == 3
                and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                and gear2_tried
            )
            # A few WAITs cover slow loads; a long run of them on an unchanged page means there is no next step.
            waits = state["history"][-8:]
            idle = len(waits) == 8 and all(h["kind"] == "wait" and h["page_changed"] is False for h in waits)
            if stuck:
                return self.stop(
                    "blocked",
                    "loop",
                    "Stuck: the last 3 actions did not change the page, even with the vision fallback. "
                    f"Repeated: {', '.join(dict.fromkeys(h['action'] for h in repeated))}.",
                )
            bounce = state["history"][-10:]
            if (
                len(bounce) == 10
                and len({h.get("url") for h in bounce}) <= 2
                and len({h["action"] for h in bounce}) <= 3
                and all(h["kind"] != "wait" for h in bounce)
            ):
                urls = " and ".join(dict.fromkeys(h.get("url") for h in bounce))
                return self.stop("blocked", "loop", f"Going back and forth between {urls} without progress.")
            if idle:
                if is_empty(state["page"]):
                    message = "The page never rendered any content, even after waiting. It may be blocked or broken."
                    return self.stop("blocked", "empty_page", message)
                return self.stop(
                    "blocked", "idle", "Waited 8 times in a row and the page never changed, so there was no next step."
                )
            state["status"] = "ready"
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def pick_file(self, action, page):
        """Upload targets come only from the user's own file list; the text helper may only name one of them."""
        names = {Path(f).name: f for f in self.files}
        if len(names) == 1:
            return next(iter(names.values())), {"model": "single-file", "latency_ms": 0, "usage": {}}
        context = field_context(self.state["goal"], action, page, self.state["history"], self.profile)
        context["files"] = sorted(names)
        name, helper = field_text(context)
        if name not in names:
            raise ValueError(f"Text helper picked a file that was not supplied: {name!r}; nothing uploaded.")
        return names[name], helper

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            try:
                yield self.command("tick")
            except StalePage:
                time.sleep(0.3)
                continue
            except MissingValue:
                raise  # The caller asks the user, then resumes.
            except Exception as exc:
                # A crash used to end the run with no reason in the panel. Record it as a stop instead.
                yield self.stop("blocked", "error", f"The agent crashed: {type(exc).__name__}: {exc}")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
