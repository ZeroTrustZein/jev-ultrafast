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
