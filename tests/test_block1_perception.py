"""Unit tests for Block 1: Universal Web Perception & Intent-Aware Input Masking."""

import pytest
from unittest.mock import Mock
from jev_ultrafast import model
from jev_ultrafast.model import systemone_request, field_text, is_unrequested_search, MissingValue


def test_unrequested_searchbox_is_detected():
    # Search box with no search intent in goal
    action = {"id": "e1", "kind": "fill", "label": "Search resources, services, and docs (G+/)", "role": "searchbox"}
    assert is_unrequested_search(action, "Click New registration to register Claude Outlook")
    assert is_unrequested_search(action, "Go to Settings and add redirect URI")

    # When goal has search intent, it should NOT be flagged as unrequested
    assert not is_unrequested_search(action, "Search for Claude Outlook in registered apps")
    assert not is_unrequested_search(action, "Find user account in search bar")
    assert not is_unrequested_search(action, "Filter by name")


def test_systemone_request_masks_unrequested_search_when_non_search_inputs_exist():
    # Page with a search bar and a real form input
    state = {
        "url": "https://portal.azure.com/",
        "title": "Azure Portal",
        "text": "App registrations",
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search resources (G+/)", "role": "searchbox", "node": 1},
            {"id": "e2", "kind": "fill", "label": "Name", "role": "textbox", "node": 2},
            {"id": "e3", "kind": "click", "label": "Register", "role": "button", "node": 3},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ]
    }
    goal = "Click Register and set name to Claude Outlook"
    body, operations, targets, controls = systemone_request(state, goal, [])

    # The search bar should be pruned from TYPE_TEXT targets
    type_targets = targets.get("TYPE_TEXT", {})
    assert len(type_targets) == 1
    assert "e1" not in [a["id"] for a in type_targets.values()]
    assert "e2" in [a["id"] for a in type_targets.values()]


def test_field_text_never_raises_missing_value_on_unrequested_search(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test-key")
    # Mock LLM returning empty text
    monkeypatch.setattr(model, "post_json", lambda *_: {"choices": [{"message": {"content": '{"text": ""}'}}]})

    context = {
        "goal": "Click New registration",
        "field": {"label": "Search resources, services, and docs (G+/)", "role": "searchbox", "value": ""},
        "page": {"title": "Azure Portal", "text": "App registrations"},
        "recent_actions": []
    }

    # Should raise ValueError (skipping search), NEVER MissingValue (which halts the agent)
    with pytest.raises(ValueError) as excinfo:
        field_text(context)
    assert not isinstance(excinfo.value, MissingValue)
    assert "Skipping unrequested search bar" in str(excinfo.value)
