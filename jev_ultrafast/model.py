"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import re
import time

import httpx

from . import local
from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=120)


def post_json(url, key, body):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


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


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT", "upload": "UPLOAD"}
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


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
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
    started = time.perf_counter()
    if os.environ.get("JEV_BACKEND") == "local":
        result = local.systemone(body, post_json)
    else:
        base = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
        result = post_json(base + "/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
    if os.environ.get("JEV_LOG"):
        # Teacher data for distillation: the exact systemone request and the full answer distributions.
        with open(os.environ["JEV_LOG"], "a", encoding="utf-8") as log:
            log.write(json.dumps({"request": body, "answers": result["answers"], "model": result.get("model")}) + "\n")
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
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
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = {"thinking": {"type": "disabled"}} if "api.deepseek.com/" in base else {"reasoning": {"effort": "low"}}
    if os.environ.get("TEXT_MODEL_REASONING") == "none":
        reasoning = {"reasoning": {"enabled": False}}
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 1024,
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
            raise MissingValue(context["field"]["label"])
        if isinstance(value, str) and value.lower().strip() in PLACEHOLDERS:
            raise MissingValue(context["field"]["label"])
        if set(output) != {"text"} or not isinstance(value, str) or len(value) > 2000:
            raise ValueError()
    except MissingValue:
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
- Never choose WAIT on an idle page. Recent WAIT actions are not loading. Prefer a useful visible control over WAIT.
- Submit populated search fields: if a search box has a value and Search/Submit is visible, CLICK it.
- Set all requested filters/controls (combobox, checkbox) before opening a result item or choosing DONE."""


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
            f"[{e['index']}] {e.get('role', '')} \"{e['label']}\" value={e.get('value', '')!r} "
            f"ops={','.join(e.get('operations', []))}"
        )
        for opt in e.get("options", []):
            lines.append(f"  [{opt['index']}] option \"{opt['label']}\"")
    elements_text = "\n".join(lines)

    user_text = f"""GOAL: {goal}
TRIGGER: {reason}
RECENT ACTIONS: {json.dumps([{k: h.get(k) for k in ('action', 'text', 'page_changed')} for h in history[-5:]])}
PAGE URL: {page.get('url', '')}
PAGE TITLE: {page.get('title', '')}
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
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": VISION_SYSTEM},
                {"role": "user", "content": content},
            ],
        },
    )
    data = json_content(result)
    latency_ms = round((time.perf_counter() - started) * 1000)

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
