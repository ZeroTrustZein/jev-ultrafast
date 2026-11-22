"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import base64
import json
import math
import os
import re
import time
import uuid
from pathlib import Path

import httpx

from . import local
from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=120)


def post_json(url, key, body, headers=None):
    current_body = dict(body)
    auth_headers = {"Authorization": f"Bearer {key}"} if key else {}
    headers = {**auth_headers, **(headers or {})}
    for _ in range(5):
        res = None
        for attempt in range(3):
            try:
                response = CLIENT.post(url, json=current_body, headers=headers)
            except httpx.HTTPError:
                raise RuntimeError("Model connection failed; no action executed.") from None
            if response.status_code in {429, 529, 503} and attempt < 2:
                time.sleep(0.5 * 2**attempt)
                continue
            if response.is_error:
                raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
            res = response.json()
            break
        if not res:
            raise RuntimeError("Model unavailable")

        choice = (res.get("choices") or [{}])[0]
        # Auto-drain server-side gateway tool calls (e.g. OmniRoute memory_save)
        if choice.get("finish_reason") == "tool_calls" and choice.get("message", {}).get("tool_calls"):
            msg = choice["message"]
            tool_results = res.get("tool_results") or []
            tool_outputs = {tr.get("tool_call_id"): tr.get("output", '{"success":true}') for tr in tool_results}
            messages = list(current_body.get("messages", []))
            messages.append(msg)
            for tc in msg.get("tool_calls", []):
                t_id = tc.get("id")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": t_id,
                        "content": tool_outputs.get(t_id, '{"success":true}'),
                    }
                )
            current_body["messages"] = messages
            continue
        return res
    return res


def json_content(result):
    """Parse chat-completion JSON, tolerating providers that wrap replies in Markdown or reasoning tokens."""
    msg = result["choices"][0]["message"]
    content = msg.get("content") or ""
    if not content:
        content = msg.get("reasoning_content") or "{}"
    if "```" in content:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            content = content[start : end + 1]
    return json.loads(content)


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        choice = answer.get("choice") if isinstance(answer, dict) else None
        raise ValueError(f"Invalid TypeSafe response (choice={choice!r}); no action executed.")
    return answer


def teacher_log(entry, page, targets, screenshot, shot=None):
    """Teacher data for a future vision model: one JSONL line per decision, screenshot saved beside the log."""
    path = Path(os.environ["JEV_LOG"])
    if screenshot:
        shot = shot or uuid.uuid4().hex
        (path.parent / "img").mkdir(parents=True, exist_ok=True)
        (path.parent / "img" / f"{shot}.jpg").write_bytes(base64.b64decode(screenshot))
        entry["image"] = f"img/{shot}.jpg"
    entry["rects"] = {t.split(":")[0]: a["rect"] for g in targets.values() for t, a in g.items() if "rect" in a}
    entry["viewport"] = {"w": page.get("w"), "h": page.get("h")}
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "coords": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT", "upload": "UPLOAD"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def is_unrequested_search(action, goal):
    lbl = (action.get("label") or "").lower()
    r = (action.get("role") or "").lower()
    is_search = r == "searchbox" or any(w in lbl for w in ("search", "filter", "find", "jump to", "g+/"))
    if not is_search:
        return False
    g = (goal or "").lower()
    has_search_intent = any(w in g for w in ("search", "find", "query", "filter", "type", "enter", "input", "write", "fill"))
    return not has_search_intent


def systemone_request(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    if "TYPE_TEXT" in targets:
        non_search = {idx: a for idx, a in targets["TYPE_TEXT"].items() if not is_unrequested_search(a, goal)}
        if non_search:
            targets["TYPE_TEXT"] = non_search
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
        "UPLOAD": "Attach one of the user's files to a file input.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        # Mask unrequested search inputs from candidates if non-search candidates exist
        if operation == "TYPE_TEXT":
            non_search = {idx: a for idx, a in candidates.items() if not is_unrequested_search(a, goal)}
            if non_search:
                candidates = non_search
        elif operation == "CLICK":
            g_low = (goal or "").lower()
            if "free" in g_low or "available" in g_low:
                free_c = {idx: a for idx, a in candidates.items() if "[occupied]" not in (a.get("label") or "").lower()}
                if free_c:
                    if "window" in g_low:
                        win_c = {idx: a for idx, a in free_c.items() if any(w in a.get("label", "").upper() for w in ("A [", "D [", "WINDOW", "A ", "D "))}
                        if win_c:
                            free_c = win_c
                    candidates = free_c
                    targets[operation] = free_c
            for color in ("red", "blue", "green", "yellow", "purple", "coral"):
                if color in g_low:
                    col_c = {idx: a for idx, a in candidates.items() if color in (a.get("label") or "").lower()}
                    if col_c:
                        candidates = col_c
                        targets[operation] = col_c
                        break
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    return body, operations, targets, controls


def pick(answers, operations, targets, controls, history=(), goal=""):
    operation_answer = validate_choice(answers.get("operation", {}), operations)
    operation = operation_answer["choice"]

    # Step 1 premature DONE suppression: if 0 actions executed and goal requires action, pick best available action
    has_action_goal = any(w in (goal or "").lower() for w in ("click", "select", "press", "open", "type", "fill", "register", "download", "remove", "add", "pick"))
    if operation == "DONE" and len(history) == 0 and has_action_goal:
        action_ops = [op for op in operations if op not in {"DONE", "WAIT", "BLOCKED"}]
        if action_ops:
            op_probs = operation_answer.get("probabilities", {})
            best_op = max(action_ops, key=lambda op: op_probs.get(op, 0.0))
            if op_probs.get(best_op, 0.0) > 0.05:
                operation = best_op

    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(answers.get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
    }


def choose(state, goal, history):
    body, operations, targets, controls = systemone_request(state, goal, history)
    screenshot = state.get("screenshot") if os.environ.get("JEV_LOG") else None
    shot = uuid.uuid4().hex
    started = time.perf_counter()
    if os.environ.get("JEV_BACKEND") == "local":
        result = local.systemone(body, post_json)
    else:
        base = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
        extra = {}
        if screenshot and os.environ.get("VISION_TEACHER"):
            # Jev 1.14 image asset plus a box head: visual teacher labels. Not used by the action choice.
            body = {
                **body,
                "state": {**body["state"], "screen": f"{shot}-screen"},
                "questions": {
                    **body["questions"],
                    "vision_box": {
                        "type": "box",
                        "instructions": f"the element the agent should act on next for goal: {goal}, in `screen`",
                    },
                },
                "assets": {f"{shot}-screen": {"type": "image", "data_base64": screenshot, "format": "jpeg"}},
            }
            extra = {"headers": {"TypeSafe-Danger-Zone-Images": "true"}}
        result = post_json(base + "/v1/systemone", os.environ["TYPESAFE_API_KEY"], body, **extra)
    if os.environ.get("JEV_LOG"):
        # Teacher data for distillation: the exact systemone request (image saved as a file) and full answers.
        request = {k: v for k, v in body.items() if k != "assets"}
        entry = {"request": request, "answers": result["answers"], "model": result.get("model"), "gear": 1}
        teacher_log(entry, state, targets, screenshot, shot)
    return {
        **pick(result["answers"], operations, targets, controls, history=history, goal=goal),
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


PERSONAL_FIELD = re.compile(
    r"\b(name|vorname|nachname|surname|family name|first name|last name|geburt|birth|passport|passnummer|"
    r"id number|identifikation|postleitzahl|plz|postal|zip|phone|telefon|mobile|handy|email|e-mail|"
    r"adresse|street|straße|strasse|nationality|staatsangehörigkeit)\b",
    re.I,
)
PLACEHOLDERS = {"n/a", "none", "unknown", "null", "john doe", "jane doe", "test", "example", "placeholder"}


class MissingValue(ValueError):
    """The goal and profile do not contain a value for this field; ask the user instead of guessing."""

    def __init__(self, field):
        super().__init__(f"No value known for '{field}'; nothing typed.")
        self.field = field


class UnrequestedSearchError(ValueError):
    """An unrequested search/filter input field was skipped."""


def field_context(goal, action, page, history, profile=None):
    return {
        "goal": goal,
        **({"profile": profile} if profile else {}),
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")

    # Fast path: direct match in known profile facts without network roundtrip
    prof = context.get("profile") or {}
    field_label = (context.get("field", {}).get("label") or "").strip().lower()
    if prof and field_label:
        clean_key = re.sub(r"[^a-z0-9_]+", "", field_label.replace(" ", "_"))
        for k in (clean_key, field_label):
            if k in prof and prof[k]:
                return str(prof[k]), {"model": "fast-profile", "latency_ms": 0, "usage": {}}

    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = {"reasoning_effort": "low"}
    if "api.deepseek.com/" in base:
        reasoning = {"thinking": {"type": "disabled"}}
    elif os.environ.get("TEXT_MODEL_REASONING") == "none":
        reasoning = {"reasoning_effort": "none"}
    started = time.perf_counter()
    val_len = len(str(context.get("field", {}).get("value", "")))
    max_tokens = 4096 if val_len > 150 else 256
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {
                    "role": "user",
                    "content": json.dumps(context),
                },
            ],
        },
    )
    try:
        output = json_content(result)
        value = output.get("text")
        if value is None or (isinstance(value, str) and not value.strip()):
            if is_unrequested_search(context.get("field", {}), context.get("goal", "")):
                raise UnrequestedSearchError("Skipping unrequested search bar; no search term requested.")
            raise MissingValue(context["field"]["label"])
        if isinstance(value, str) and value.lower().strip() in PLACEHOLDERS:
            if is_unrequested_search(context.get("field", {}), context.get("goal", "")):
                raise UnrequestedSearchError("Skipping unrequested search bar; placeholder found.")
            raise MissingValue(context["field"]["label"])
        if set(output) != {"text"} or not isinstance(value, str) or len(value) > 100000:
            raise ValueError()
    except (MissingValue, UnrequestedSearchError):
        raise
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }


VISION_SYSTEM = """You are Gear 2 of a hybrid browser agent. The fast local policy is stuck, low-confidence, or blocked.
Analyze the page screenshot and numbered element table to choose the next action.
Return a JSON object:
{
  "operation": "CLICK"|"TYPE_TEXT"|"SELECT"|"UPLOAD"|"CLICK_COORDS"|"SCROLL_DOWN"|"WAIT"|"DONE"|"BLOCKED",
  "target": "element index string like '3' for table elements",
  "x": integer (required only if CLICK_COORDS for visual controls not in the element table),
  "y": integer (required only if CLICK_COORDS),
  "text": "text string if TYPE_TEXT",
  "reason": "one sentence explaining the choice"
}
RULES:
- Prefer choosing target from the table. Use CLICK_COORDS only for visual controls (canvas, map, icon).
- SEAT MAPS: For seating charts, maps, or canvas widgets without table indices,
  choose CLICK_COORDS with the exact (x, y) coordinates of the seat/control in CSS pixels. Select central seats.
- Never choose WAIT on an idle page. Recent WAIT actions are not loading. Prefer a useful visible control over WAIT.
- Submit populated search fields: if a search box has a value and Search/Submit is visible, CLICK it.
- Set all requested filters/controls (combobox, checkbox) before opening a result item or choosing DONE.
- Date pickers: if the date input accepts text, TYPE_TEXT the full date instead of clicking month arrows.
- DONE only when the page visibly shows every place, value and item named in the goal (both ends of a route, the
  converted amount, the opened result itself rather than a results list). Otherwise act."""


def choose_vision(page, goal, history, screenshot_b64, reason="low confidence"):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("Gear 2 vision model requires TEXT_MODEL_API_KEY")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "http://127.0.0.1:20128").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "claude-odysseus")

    elements, targets, controls = action_space(page["actions"])
    lines = []
    for e in elements[:120]:
        lines.append(
            f'[{e["index"]}] {e.get("role", "")} "{e["label"]}" value={e.get("value", "")!r} '
            f"ops={','.join(e.get('operations', []))}"
        )
        for opt in e.get("options", []):
            lines.append(f'  [{opt["index"]}] option "{opt["label"]}"')
    elements_text = "\n".join(lines)

    user_text = f"""GOAL: {goal}
TRIGGER: {reason}
VIEWPORT: width={page.get("w", 1200)}, height={page.get("h", 800)} (ensure CLICK_COORDS x, y are within this range)
RECENT ACTIONS: {json.dumps([{k: h.get(k) for k in ("action", "text", "page_changed")} for h in history[-5:]])}
PAGE URL: {page.get("url", "")}
PAGE TITLE: {page.get("title", "")}
ELEMENT TABLE:
{elements_text}
"""
    started = time.perf_counter()
    content = [
        {"type": "text", "text": user_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
    ]
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 1024,
            "reasoning_effort": "low",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": VISION_SYSTEM},
                {"role": "user", "content": content},
            ],
        },
    )
    data = json_content(result)
    latency_ms = round((time.perf_counter() - started) * 1000)
    if os.environ.get("JEV_LOG"):
        entry = {"prompt": user_text, "output": data, "model": f"vision:{model}", "gear": 2}
        teacher_log(entry, page, targets, screenshot_b64)

    op = str(data.get("operation", "WAIT")).upper()
    target = str(data.get("target", ""))

    choice = None
    if op == "CLICK_COORDS":
        choice = "CLICK_COORDS"
    elif op in targets:
        act = targets[op].get(target)
        if not act:
            act = next((a for a in targets[op].values() if a["id"].lower() == target.lower()), None)
        if not act:
            act = next((a for a in targets[op].values() if a["label"].lower().startswith(target.lower())), None)
        if act:
            choice = act["id"]

    if not choice:
        if op in controls:
            choice = controls[op]["id"]
        elif op.lower() in [a["id"] for a in page.get("actions", [])]:
            choice = op.lower()
        elif op in {"DONE", "BLOCKED"}:
            choice = op
        elif op == "WAIT":
            choice = "wait"
        else:
            matched = next(
                (a for a in page.get("actions", []) if a["id"].lower() == target.lower()),
                None,
            )
            has_wait = any(a["id"] == "wait" for a in page.get("actions", []))
            choice = matched["id"] if matched else ("wait" if has_wait else op)

    return {
        "choice": choice,
        "operation": op,
        "target": target,
        "x": data.get("x"),
        "y": data.get("y"),
        "text": data.get("text"),
        "reason": data.get("reason", ""),
        "confidence": 1.0,
        "probabilities": {choice: 1.0},
        "latency_ms": latency_ms,
        "gear": 2,
        "model": f"vision:{model}",
        "usage": result.get("usage", {}),
    }


def extract_box_center(box, w=1200, h=800):
    if isinstance(box, dict):
        if "left" in box and "right" in box:
            t, b = box.get("top", 0), box.get("bottom", 0)
            l, r = box.get("left", 0), box.get("right", 0)
            cx = (l + r) / 2 * w
            cy = (t + b) / 2 * h
        elif "x" in box and "width" in box:
            cx = (box["x"] + box["width"] / 2) * w
            cy = (box["y"] + box["height"] / 2) * h
        else:
            return w // 2, h // 2
    elif isinstance(box, (list, tuple)) and len(box) == 4:
        t, b, l, r = box
        cx = (l + r) / 2 * w
        cy = (t + b) / 2 * h
    else:
        return w // 2, h // 2
    return min(max(int(round(cx)), 0), w - 1), min(max(int(round(cy)), 0), h - 1)


def choose_vision_jev(page, goal, history, screenshot_b64, reason="low confidence", base=None):
    """Gear 2 on Jev 1.14 vision: Gear 1's questions plus CLICK_COORDS, an existence check, and a box.
    `base` overrides the TypeSafe URL (e.g. a local Laya server, which needs no key)."""
    body, operations, targets, controls = systemone_request(page, goal, history)
    operations["CLICK_COORDS"] = "Click a visual target with no matching element, e.g. a canvas, map, or icon."
    screen = f"{uuid.uuid4()}-screen"
    body["model"] = os.environ.get("TYPESAFE_MODEL", "jev-1.14.0")
    body["assets"] = {screen: {"type": "image", "data_base64": screenshot_b64, "format": "jpeg"}}
    body["state"].update(screen=screen, trigger=reason)
    body["questions"]["target_exists"] = {
        "type": "noul",
        "instructions": f"Is the visual target needed for this goal visible in `screen`? Goal: {goal}",
    }
    body["questions"]["target_box"] = {
        "type": "box",
        "instructions": f"The visual target to click next in `screen` for this goal: {goal}",
    }
    key = "" if base else os.environ["TYPESAFE_API_KEY"]
    base = (base or os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")).rstrip("/")
    started = time.perf_counter()
    result = post_json(base + "/v1/systemone", key, body, headers={"TypeSafe-Danger-Zone-Images": "true"})
    answers = result["answers"]
    if "CLICK_COORDS" not in answers.get("operation", {}).get("probabilities", {}):
        answers["operation"]["probabilities"]["CLICK_COORDS"] = 0.0
    if os.environ.get("JEV_LOG"):
        request = {k: v for k, v in body.items() if k != "assets"}
        entry = {"request": request, "answers": answers, "model": result.get("model"), "gear": 2}
        teacher_log(entry, page, targets, screenshot_b64, screen.replace("-screen", ""))
    decision = pick(answers, operations, targets, controls, history=history, goal=goal)
    x = y = None

    has_action_goal = any(w in (goal or "").lower() for w in ("click", "select", "press", "download", "open", "remove", "pick"))
    click_targets = targets.get("CLICK", {})
    # If text policy chose premature DONE or BLOCKED on step 1, but vision found a target box:
    if "target_box" in answers and ((decision["operation"] in {"DONE", "BLOCKED"} and has_action_goal and len(history) == 0 and not click_targets) or decision["operation"] == "CLICK_COORDS"):
        box = answers["target_box"].get("box", answers["target_box"])
        w, h = page.get("w", 1200), page.get("h", 800)
        bx, by = extract_box_center(box, w, h)
        if bx is not None and by is not None:
            # Check target exists if present
            exists = answers.get("target_exists", {})
            val = exists.get("choice", exists.get("value", exists.get("noul", True)))
            is_present = val is True or (isinstance(val, (int, float)) and val > 0) or str(val).lower() in {"true", "yes", "likely"}
            if is_present:
                decision["operation"] = "CLICK_COORDS"
                decision["choice"] = "CLICK_COORDS"
                decision["probabilities"] = {"CLICK_COORDS": 1.0}
                x, y = bx, by
            else:
                decision.update(choice="BLOCKED", operation="BLOCKED", probabilities={"BLOCKED": 1.0})
    return {
        **decision,
        "x": x,
        "y": y,
        "text": None,
        "reason": reason,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "gear": 2,
        "model": f"vision:{result.get('model', body['model'])}",
        "usage": result.get("usage", {}),
    }
