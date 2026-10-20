"""Unit tests for System 2 locked data contracts."""

from jev_ultrafast.contracts import SubGoal, OutcomeEvaluation, normalize_subgoals


def test_subgoal_serialization():
    sg = SubGoal(
        goal="Search 'RTX 4080 Super' and click add to cart",
        start_url="https://www.amazon.com/s?k=RTX+4080+Super",
        completion_criteria="Item added to cart",
    )
    d = sg.to_dict()
    assert d["goal"] == "Search 'RTX 4080 Super' and click add to cart"
    assert d["start_url"] == "https://www.amazon.com/s?k=RTX+4080+Super"
    assert d["completion_criteria"] == "Item added to cart"

    rebuilt = SubGoal.from_dict(d)
    assert rebuilt == sg


def test_outcome_evaluation_serialization():
    out = OutcomeEvaluation(
        completed=False,
        summary="Landed on YouTube video page instead of channel.",
        diverged=True,
        corrective_action="Click channel avatar link below video player.",
    )
    d = out.to_dict()
    assert d["completed"] is False
    assert d["diverged"] is True
    assert "channel avatar" in d["corrective_action"]

    rebuilt = OutcomeEvaluation.from_dict(d)
    assert rebuilt == out


def test_normalize_subgoals_handles_both_schemas():
    # 1. New schema: list of sub_goals
    new_plan = {
        "reply": "Building PC on Amazon",
        "action_required": True,
        "sub_goals": [
            {"goal": "Search GPU", "start_url": "https://amazon.com/s?k=GPU", "completion_criteria": "GPU carted"},
            {"goal": "Search CPU", "start_url": "https://amazon.com/s?k=CPU", "completion_criteria": "CPU carted"},
        ]
    }
    sgs = normalize_subgoals(new_plan)
    assert len(sgs) == 2
    assert sgs[0].goal == "Search GPU"
    assert sgs[1].goal == "Search CPU"

    # 2. Legacy schema: flat goal and start_url
    legacy_plan = {
        "reply": "Opening YouTube",
        "action_required": True,
        "start_url": "https://youtube.com/@Caedrel",
        "goal": "Verify channel title"
    }
    sgs_legacy = normalize_subgoals(legacy_plan)
    assert len(sgs_legacy) == 1
    assert sgs_legacy[0].goal == "Verify channel title"
    assert sgs_legacy[0].start_url == "https://youtube.com/@Caedrel"
