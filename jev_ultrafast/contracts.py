"""Strict data contracts for System 2 Reasoning & Sub-Goal Decomposition.

Locked pre-flight contract across Subagents Alpha through Zeta.
"""

from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any


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


def evidence_holds(evidence, page_text) -> bool:
    """A verifier's "done" counts only if every quoted piece of evidence is really on the page.

    Verifiers approved pages that did not satisfy the goal (xe.com with no conversion, a GitHub topics page).
    Requiring verbatim quotes that code can find keeps a model from approving what is not visible.
    """
    quotes = [evidence] if isinstance(evidence, str) else list(evidence or [])
    quotes = [" ".join(str(q).split()).lower() for q in quotes if str(q).strip()]
    text = " ".join(str(page_text or "").split()).lower()
    return bool(quotes) and all(len(q) >= 3 and q in text for q in quotes)


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
