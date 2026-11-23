"""Strict data contracts for System 2 Reasoning & Sub-Goal Decomposition.

Locked pre-flight contract across Subagents Alpha through Zeta.
"""

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class SubGoal:
    goal: str
    start_url: Optional[str] = None
    completion_criteria: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "start_url": self.start_url,
            "completion_criteria": self.completion_criteria or self.goal,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubGoal":
        goal = str(data.get("goal") or "").strip()
        start_url = data.get("start_url")
        if start_url:
            start_url = str(start_url).strip()
            if not start_url or start_url.lower() in {"null", "none", ""}:
                start_url = None
        criteria = str(data.get("completion_criteria") or goal).strip()
        return cls(goal=goal, start_url=start_url, completion_criteria=criteria)


@dataclass
class OutcomeEvaluation:
    completed: bool
    summary: str
    diverged: bool = False
    corrective_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "completed": bool(self.completed),
            "summary": self.summary,
            "diverged": bool(self.diverged),
            "corrective_action": self.corrective_action,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OutcomeEvaluation":
        completed = bool(data.get("completed", False))
        summary = str(data.get("summary") or "").strip()
        diverged = bool(data.get("diverged", not completed))
        corrective_action = data.get("corrective_action")
        if corrective_action:
            corrective_action = str(corrective_action).strip()
        return cls(
            completed=completed,
            summary=summary,
            diverged=diverged,
            corrective_action=corrective_action,
        )


# Stop states where the agent ran out of road, not ones where it refused: the goal may already be on screen.
GAVE_UP = {"loop", "call_budget", "step_limit", "idle"}


def _plain(text) -> str:
    """Lowercase words only, so quotes survive punctuation and whitespace differences ("·", "-", line breaks)."""
    return " ".join(re.sub(r"[^\w]+", " ", str(text or "").lower()).split())


def evidence_holds(evidence, page_text, *also) -> bool:
    """A verifier's "done" counts only if every quoted piece of evidence is really on the page.

    Verifiers approved pages that did not satisfy the goal (xe.com with no conversion, a GitHub topics page).
    Requiring quotes that code can find keeps a model from approving what is not visible. The page title and URL
    count as page content too (the verifier sees them), and a quote with "..." must match piece by piece.
    """
    quotes = [evidence] if isinstance(evidence, str) else list(evidence or [])
    pieces = [_plain(p) for q in quotes for p in re.split(r"\.\.\.|…", str(q))]
    pieces = [p for p in pieces if p]
    text = _plain(" ".join(str(t or "") for t in (page_text, *also)))
    return bool(pieces) and all(len(p) >= 3 and p in text for p in pieces)


_DATE_LIKE = re.compile(r"^[\d\s./:-]+$")
_STOP = {"the", "and", "for", "from", "with", "page", "search", "results"}


def goal_values(goal) -> list:
    """Concrete values the goal quotes ('Louvre Museum, Paris', 'usb-c cable'). Numeric dates are skipped: sites
    render them in their own format."""
    # Quote marks must not touch letters, so apostrophes ("user's", "don't") never open or close a value.
    values = re.findall(r"(?<!\w)['\"‘“]([^'\"’”]{2,80})['\"’”](?!\w)", str(goal or ""))
    return [v for v in values if not _DATE_LIKE.match(v)]


def missing_goal_values(goal, page) -> list:
    """Quoted goal values with no trace on the page (text, title, URL or field values).

    A value counts as present when at least half of its meaningful words appear. Substring matching tolerates
    localisation such as 'Eiffel' inside 'Eiffelturm'. The Google Maps verifier accepted a route that never
    showed the destination; this catches that without asking a model.
    """
    fields = " ".join(str(a.get("value") or "") for a in page.get("actions", []))
    hay = _plain(" ".join(str(page.get(k) or "") for k in ("text", "title", "url")) + " " + fields)
    hay_compact = hay.replace(" ", "")
    missing = []
    for value in goal_values(goal):
        words = [w for w in _plain(value).split() if len(w) >= 3 and w not in _STOP] or _plain(value).split()
        found = sum(1 for w in words if w in hay or w in hay_compact)
        if words and found * 2 < len(words):
            missing.append(value)
    return missing


_PROHIBITION = re.compile(r"^(read[- ]only|do not|don't|dont|never|no |not |avoid|without|must not|stop before)", re.I)


def requirement_check(check) -> dict:
    """Normalise a verifier reply. With a per-requirement list, done needs every requirement met and quoted.

    Listing requirements one by one makes the verifier look at each value (xe.com: USD->EUR, not EUR->INR) instead of
    approving a page that only looks similar. The quotes become the evidence that evidence_holds checks.
    """
    check = dict(check or {}) if isinstance(check, dict) else {}
    # Prohibitions ("Read-only: do not log in") can never be quoted from a page, so they are not requirements here.
    # A violated one shows up as a wrong page instead (a login form, a submitted order).
    reqs = [
        r for r in check.get("requirements") or []
        if isinstance(r, dict) and not _PROHIBITION.match(str(r.get("need") or "").strip())
    ]
    if reqs:
        unmet = [str(r.get("need") or "?") for r in reqs if not r.get("met") or not str(r.get("quote") or "").strip()]
        check["evidence"] = [str(r.get("quote")) for r in reqs if str(r.get("quote") or "").strip()]
        if unmet:
            check["done"] = False
            check["missing"] = check.get("missing") or "Not shown: " + "; ".join(unmet)
    return check


def field_values(page) -> str:
    """Typed form values as text. Page text omits what is inside inputs, so a filled form looked empty to verifiers."""
    lines = [
        f"{a.get('label')}: {a.get('value')}"
        for a in page.get("actions", [])
        if a.get("kind") in {"fill", "select"} and str(a.get("value") or "").strip()
    ]
    # Ticked boxes and chosen radios are form state too; the verifier rejected a filled form for not showing them.
    lines += [
        f"{a.get('label')}: checked"
        for a in page.get("actions", [])
        if a.get("role") in {"checkbox", "radio", "switch"} and a.get("checked") in {True, "true", "mixed"}
    ]
    return "\n".join(dict.fromkeys(lines))


def _value_present(value, hay, share=0.5) -> bool:
    words = [w for w in _plain(value).split() if len(w) >= 3 and w not in _STOP] or _plain(value).split()
    compact = hay.replace(" ", "")
    found = sum(1 for w in words if w in hay or w in compact)
    return bool(words) and found >= max(1, share * len(words))


def goal_named_elements(goal, elements) -> list:
    """Observed elements whose label matches a value quoted in the goal ('Select Date' -> the date field)."""
    values = goal_values(goal)
    return [e for e in elements if any(_value_present(v, _plain(e.get("label"))) for v in values)][:8]


def subject_missing(goal, page) -> bool:
    """True when the goal quotes values but none of them is the page's subject (title, URL, form fields).

    The weather.com home page showed a London card in its body and was accepted as the London forecast page.
    The pages that really are the goal carry it in the title or URL (search results ?q=, 'London Daily Forecast',
    'The Matrix (1999) - IMDb') or in the form the goal asked to fill.
    """
    values = goal_values(goal)
    if not values:
        return False
    # Form fields count (the goal may be "fill this form"), but a search box does not: typing "London" into it
    # does not make the page about London.
    fields = " ".join(
        f"{a.get('label')} {a.get('value') or ''}"
        for a in page.get("actions", [])
        if a.get("kind") in {"fill", "select"}
        and "search" not in f"{a.get('label')} {a.get('role')}".lower()
    )
    hay = _plain(" ".join(str(page.get(k) or "") for k in ("title", "url")).replace("+", " ") + " " + fields)
    # Loose on purpose: one distinctive word of any quoted value in the title, URL or fields is enough.
    return not any(_value_present(v, hay, share=0) for v in values)


# Below this much text a page is a shell (header and nav only) or a page still rendering, never a finished goal.
THIN_PAGE_CHARS = 300


def content_missing(page) -> bool:
    """True when the page shows almost nothing besides chrome and no filled form, so a "done" cannot be judged.

    Amazon's search URL was accepted while only its 144-character header had rendered: the evidence was the
    query in the search box and the title, with no results on screen."""
    if field_values({"actions": [a for a in page.get("actions", []) if "search" not in str(a.get("label")).lower()]}):
        return False
    return len(str(page.get("text") or "").strip()) < THIN_PAGE_CHARS


def page_key(page) -> tuple:
    """Identity of what a verifier would see: the URL plus the start of the text (results loading changes it)."""
    return (page.get("url"), hash(str(page.get("text") or "")[:3000]))


def normalize_subgoals(plan_data: Dict[str, Any], user_text: str = "") -> List[SubGoal]:
    """Parse sub_goals from plan data with backward-compatibility for legacy single goals."""
    sub_goals_raw = plan_data.get("sub_goals")
    if isinstance(sub_goals_raw, list) and sub_goals_raw:
        parsed = []
        for item in sub_goals_raw:
            if isinstance(item, dict) and item.get("goal"):
                parsed.append(SubGoal.from_dict(item))
            elif isinstance(item, str) and item.strip():
                parsed.append(SubGoal(goal=item.strip(), completion_criteria=item.strip()))
        if parsed:
            return parsed

    # Backward-compatible fallback: single goal string
    legacy_goal = (plan_data.get("goal") or user_text or "").strip()
    if legacy_goal:
        return [
            SubGoal(
                goal=legacy_goal,
                start_url=plan_data.get("start_url"),
                completion_criteria=legacy_goal,
            )
        ]
    return []
