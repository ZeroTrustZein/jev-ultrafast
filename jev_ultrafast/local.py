"""Local stand-in for TypeSafe systemone: same request body in, same answers out, via Ollama.

One grammar-constrained request picks the operation and only a compatible target, so a target can
never belong to another operation. Confidence comes from the model's own token log-probabilities.
"""

import json
import math
import os

from .questions import NEXT_ACTION, TARGET

SYSTEM = f"""You control a web browser one step at a time. Choose the single next operation and its target.
{NEXT_ACTION}
{TARGET}
Answer with JSON only: {{"done": ..., "next": ..., "operation": ..., "target": ...}}.
done: goal steps already completed, judged from RECENT ACTIONS and current values (a few words, or "nothing").
next: the one remaining step to do now (a few words). Then the operation and target that perform it.
CLICK on a text field only focuses it; it never submits. After typing a query, pick a matching suggestion
or press the search/submit button. Once the page visibly shows the goal's end state (for example the
requested article or item is open and every filter/value is set), choose DONE instead of clicking further.
target is an element index from ELEMENTS."""


def prompt(body):
    state = body["state"]
    operations = body["questions"]["operation"]["criteria"]
    lines = []
    for e in state["elements"]:
        extra = "".join(f" {k}={e[k]}" for k in ("checked", "selected", "expanded") if k in e)
        value = json.dumps(e.get("value", ""), ensure_ascii=False)
        ops = ",".join(e["operations"])
        lines.append(f"[{e['index']}] {e.get('role', '')} \"{e['label']}\" value={value}{extra} ops={ops}")
        lines += [f"  [{o['index']}] option \"{o['label']}\"" for o in e.get("options", [])]
    page = state["page"]
    return "\n".join([
        "GOAL:", body["questions"]["operation"]["instructions"]["goal"],
        "", "OPERATIONS:", *(f"{k}: {v}" for k, v in operations.items()),
        "", "RECENT ACTIONS:", json.dumps(state["recent_actions"], ensure_ascii=False),
        "", f"PAGE: {page['title']} {page['url']}", page["text"],
        "", "ELEMENTS:", *lines,
    ])


def schema(body):
    branches = []
    for operation in body["questions"]["operation"]["criteria"]:
        head = body["questions"].get(operation.lower() + "_target")
        # A few words of done/next before committing makes small models track multi-step goals.
        properties = {"done": {"type": "string", "maxLength": 100}, "next": {"type": "string", "maxLength": 80},
                      "operation": {"const": operation}}
        if head:
            properties["target"] = {"enum": list(head["criteria"])}
        branches.append({"type": "object", "properties": properties, "required": list(properties)})
    return {"anyOf": branches}


def systemone(body, post_json):
    model = os.environ.get("LOCAL_MODEL", "qwen3:4b-instruct")
    base = os.environ.get("LOCAL_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    result = post_json(base + "/api/chat", "local", {
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "format": schema(body),
        "logprobs": True,
        "options": {"temperature": 0, "num_ctx": int(os.environ.get("LOCAL_NUM_CTX", 8192))},
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt(body)}],
    })
    content = result["message"]["content"]
    output = json.loads(content)
    # Score only tokens inside the chosen values; forced JSON syntax tokens say nothing about the choice.
    spans = []
    for key in ("operation", "target"):
        if key in output:
            begin = content.find(f'"{output[key]}"', content.find(f'"{key}"') + len(key) + 2) + 1
            spans.append((begin, begin + len(output[key])))
    logprob, position = 0.0, 0
    for token in result.get("logprobs") or []:
        start, position = position, position + len(token["token"])
        if any(start < end and position > begin for begin, end in spans):
            logprob += token["logprob"]
    confidence = math.exp(logprob)
    answers = {}
    # Only the selected operation's target head is answered; choose() validates exactly that head.
    for name, choice in (("operation", output["operation"]), (output["operation"].lower() + "_target",
                                                              output.get("target"))):
        question = body["questions"].get(name)
        if not question:
            continue
        answers[name] = {
            "choice": choice,
            "confidence": confidence,
            "probabilities": {k: float(k == choice) for k in question["criteria"]},
        }
    usage = {"done": output.get("done"), "next": output.get("next"),
             "input_tokens": result.get("prompt_eval_count"), "output_tokens": result.get("eval_count"),
             "prefill_ms": round(result.get("prompt_eval_duration", 0) / 1e6),
             "decode_ms": round(result.get("eval_duration", 0) / 1e6)}
    return {"answers": answers, "model": f"local:{model}", "usage": usage}
