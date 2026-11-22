"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import base64
import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(
        0,
        {
            "id": "toggle",
            "kind": "click",
            "label": "Free cancellation",
            "node": 30,
            "role": "checkbox",
            "checked": "true",
            "selected": False,
        },
    )

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation(
            {
                "operation": "act",
                "session": "test",
                "action": {
                    "id": "e1",
                    "kind": "select",
                    "node": 1,
                    "value": "Design",
                },
            }
        )
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def ollama_reply(content):
    tokens = [{"token": ch, "logprob": -0.1 if ch.isalnum() else -5.0} for ch in content]
    return {"message": {"content": content}, "logprobs": tokens, "prompt_eval_count": 9, "eval_count": 3}


def test_local_backend_returns_typesafe_shape_through_choose(monkeypatch):
    sent = []

    def post(url, _key, body):
        sent.append((url, body))
        return ollama_reply('{"done": "nothing", "next": "click Go", "operation": "CLICK", "target": "2"}')

    monkeypatch.setenv("JEV_BACKEND", "local")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    url, body = sent[0]
    assert len(sent) == 1 and url.endswith("/api/chat")
    branches = {b["properties"]["operation"]["const"]: b["properties"] for b in body["format"]["anyOf"]}
    # A CLICK branch can only name click targets; TYPE_TEXT only editable ones; controls take no target.
    assert branches["CLICK"]["target"]["enum"] == ["1", "2"] and branches["TYPE_TEXT"]["target"]["enum"] == ["1"]
    assert "target" not in branches["WAIT"] and "target" not in branches["DONE"]
    assert d["choice"] == "e3" and d["operation"] == "CLICK" and d["target"] == "2"
    # Confidence ignores forced JSON syntax (-5.0 per char) and scores only the chosen values.
    assert abs(d["confidence"] - 2.718281828**-0.6) < 1e-6


def test_local_backend_rejects_target_outside_the_operation(monkeypatch):
    monkeypatch.setenv("JEV_BACKEND", "local")
    monkeypatch.setattr(
        model, "post_json", lambda *_: ollama_reply('{"done": "", "next": "", "operation": "TYPE_TEXT", "target": "2"}')
    )
    with pytest.raises(ValueError, match="no action executed"):
        model.choose(page(), "Find a book", [])


def submit_page():
    p = page()
    p["actions"][2] = {
        "id": "e3",
        "kind": "click",
        "label": "Submit application",
        "role": "button",
        "value": "",
        "node": 20,
    }
    p["fingerprint"] = fingerprint(p)
    return p


@pytest.mark.parametrize("allowed", [True, False])
def test_sensitive_click_needs_approval_before_input(runner, allowed):
    runner.state["page"] = submit_page()
    runner.state["decision"] = {**decision("e3"), "operation": "CLICK", "probabilities": {"e3": 1.0}}
    runner.approve = Mock(return_value=allowed)
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.approve.call_args.args[0]["label"] == "Submit application"
    assert runner.state["browser"].act.called is allowed
    assert (runner.state["status"] == "blocked") is not allowed


def test_missing_text_value_asks_instead_of_guessing(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", lambda *_: {"choices": [{"message": {"content": '{"text": null}'}}]})
    context = model.field_context("Fill the form", page()["actions"][0], page(), [], {"name": "Ada"})
    assert context["profile"] == {"name": "Ada"}
    with pytest.raises(model.MissingValue) as missing:
        model.field_text(context)
    assert missing.value.field == "Search"


def test_planner_never_verifies_a_blocked_run(monkeypatch):
    from jev_ultrafast import planner

    asked = []
    monkeypatch.setattr(
        planner,
        "ask",
        lambda system, _content: (
            asked.append(system)
            or {"url": "https://example.test/", "goal": "Find a book", "profile": {}, "done": True, "missing": ""}
        ),
    )
    fake = Mock(state={"status": "blocked", "page": page()}, snapshot=Mock(return_value={"page": page()}))
    fake.__enter__ = Mock(return_value=fake)
    fake.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(planner, "Agent", Mock(return_value=fake))
    result = planner.run("Find a book", report=lambda _: None)
    assert result["done"] is False and asked == [planner.PLAN, planner.VERIFY]  # only the arrival check


def test_fenced_json_reply_is_accepted(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    reply = '```json\n{"text": "Lisbon"}\n```'
    monkeypatch.setattr(model, "post_json", lambda *_: {"choices": [{"message": {"content": reply}}]})
    assert model.field_text(model.field_context("Stay in Lisbon", page()["actions"][0], page(), []))[0] == "Lisbon"


def test_action_space_includes_upload():
    actions = [
        {"id": "e1", "kind": "upload", "label": "Upload ID", "role": "upload", "value": "", "node": 15},
        {"id": "e2", "kind": "click", "label": "Next", "role": "button", "value": "", "node": 25},
    ]
    elements, targets, controls = model.action_space(actions)
    assert "UPLOAD" in targets
    assert targets["UPLOAD"]["1"]["label"] == "Upload ID"
    assert elements[0]["operations"] == ["UPLOAD"]


def test_pdf_document_extraction(tmp_path):
    from pypdf import PdfWriter

    from jev_ultrafast.planner import documents

    pdf_file = tmp_path / "test_doc.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    # create a simple PDF with text or extract check
    with open(pdf_file, "wb") as f:
        writer.write(f)

    # Gracefully extracts without crashing on scanned or image-based PDFs
    doc_parts = documents([pdf_file])
    assert len(doc_parts) >= 1
    assert "test_doc.pdf" in doc_parts[0]["text"]


def test_browser_finder_discovers_chrome_or_brave():
    from jev_ultrafast.browser import find_browser

    exe, kind = find_browser()
    assert exe is not None
    assert kind in ("chrome", "brave")


def test_gear2_triggers_on_low_confidence(runner, monkeypatch):
    monkeypatch.setenv("GEAR2", "1")
    monkeypatch.setenv("GEAR2_BACKEND", "vlm")
    monkeypatch.setenv("GEAR2_CONFIDENCE_THRESHOLD", "0.7")
    monkeypatch.setattr(
        loop,
        "choose",
        lambda *_: {
            "choice": "e1",
            "operation": "TYPE_TEXT",
            "target": "1",
            "confidence": 0.5,
            "probabilities": {"e1": 1.0},
            "latency_ms": 10,
            "usage": {},
        },
    )
    runner.state["browser"].call.return_value = {"data": "fake_jpeg_b64"}

    vision_called = []

    def mock_vision(page, goal, history, screenshot, reason):
        vision_called.append((reason, screenshot))
        return {
            "choice": "e3",
            "operation": "CLICK",
            "target": "2",
            "confidence": 1.0,
            "probabilities": {"e3": 1.0},
            "latency_ms": 50,
            "gear": 2,
            "model": "mock_vlm",
            "usage": {},
        }

    monkeypatch.setattr(loop, "choose_vision", mock_vision)
    runner.command("predict")

    assert len(vision_called) == 1
    assert vision_called[0][0] == "low confidence"
    assert runner.state["decision"]["gear"] == 2
    assert runner.state["decision"]["choice"] == "e3"


def test_gear2_click_coords_dispatches_mouse_event(runner):
    runner.state["decision"] = {
        "choice": "CLICK_COORDS",
        "operation": "CLICK_COORDS",
        "x": 150,
        "y": 250,
        "confidence": 1.0,
        "probabilities": {"CLICK_COORDS": 1.0},
        "latency_ms": 10,
        "gear": 2,
        "usage": {},
    }
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    act_arg = runner.state["browser"].act.call_args.args[0]
    assert act_arg["kind"] == "coords"
    assert act_arg["x"] == 150 and act_arg["y"] == 250
    assert runner.state["history"][-1]["gear"] == 2


JEV_OPS = ["TYPE_TEXT", "CLICK", "WAIT", "DONE", "BLOCKED", "CLICK_COORDS"]


def jev_vision(monkeypatch, exists, box=None):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    answers = {
        "operation": choice(JEV_OPS, "CLICK_COORDS"),
        "target_exists": {"type": "noul", "choice": exists},
        "target_box": {"type": "box", "box": box or {"top": 0.25, "bottom": 0.75, "left": 0.4, "right": 0.6}},
    }
    post = Mock(return_value={"answers": answers, "model": "jev-1.14.0"})
    monkeypatch.setattr(model, "post_json", post)
    p = {**page(), "w": 1000, "h": 800}
    return model.choose_vision_jev(p, "Pick a seat", [], "abc123", reason="low confidence"), post


def test_gear2_jev_request_carries_screen_asset_and_vision_questions(monkeypatch):
    _, post = jev_vision(monkeypatch, True)
    url, _key, body = post.call_args.args
    assert url.endswith("/v1/systemone")
    assert post.call_args.kwargs["headers"] == {"TypeSafe-Danger-Zone-Images": "true"}
    screen = body["state"]["screen"]
    assert screen.endswith("-screen")
    assert body["assets"][screen] == {"type": "image", "data_base64": "abc123", "format": "jpeg"}
    assert body["questions"]["target_exists"]["type"] == "noul"
    assert body["questions"]["target_box"]["type"] == "box"
    assert "Pick a seat" in body["questions"]["target_box"]["instructions"]
    assert "CLICK_COORDS" in body["questions"]["operation"]["criteria"]


def test_gear2_jev_box_converts_to_clamped_viewport_pixels(monkeypatch):
    d, _ = jev_vision(monkeypatch, True)
    assert (d["choice"], d["x"], d["y"], d["gear"]) == ("CLICK_COORDS", 500, 400, 2)
    d, _ = jev_vision(monkeypatch, True, {"top": 1.0, "bottom": 1.2, "left": -0.4, "right": 0.0})
    assert (d["x"], d["y"]) == (0, 799)


def test_gear2_jev_absent_target_blocks(monkeypatch):
    d, _ = jev_vision(monkeypatch, False)
    assert d["choice"] == "BLOCKED" and d["x"] is None


@pytest.mark.parametrize("backend", ["jev", "vlm", "laya"])
def test_gear2_backend_switch(monkeypatch, backend):
    monkeypatch.setenv("GEAR2_BACKEND", backend)
    monkeypatch.delenv("GEAR2_LAYA_URL", raising=False)
    jev, vlm = Mock(return_value="jev"), Mock(return_value="vlm")
    monkeypatch.setattr(loop, "choose_vision_jev", jev)
    monkeypatch.setattr(loop, "choose_vision", vlm)
    assert loop.gear2(page(), "g", [], "s", "r") == ("vlm" if backend == "vlm" else "jev")
    if backend != "vlm":
        assert jev.call_args.kwargs["base"] == ("http://127.0.0.1:8791" if backend == "laya" else None)


def test_endless_waits_on_unchanged_page_stop_the_run(runner):
    for _ in range(8):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked"


def test_confirmation_checkbox_is_not_gated_but_submit_button_is(runner):
    p = page()
    p["actions"][2] = {
        "id": "e3",
        "kind": "click",
        "label": "Confirm information is accurate",
        "role": "checkbox",
        "value": "on",
        "node": 20,
    }
    p["fingerprint"] = fingerprint(p)
    runner.state["page"] = p
    runner.state["decision"] = {**decision("e3"), "operation": "CLICK", "probabilities": {"e3": 1.0}}
    runner.approve = Mock(return_value=False)
    runner.command("act", {"fingerprint": p["fingerprint"]})
    runner.approve.assert_not_called()
    assert runner.state["browser"].act.called


def test_chat_turn_prominently_displays_current_active_tab(monkeypatch):
    from jev_ultrafast.chat_agent import chat_turn

    calls = []

    def mock_post(_url, _key, body):
        calls.append(body)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "reply": "Opening editor and inserting architect Nazih Dergham.",
                                "action_required": True,
                                "start_url": "https://en.wikipedia.org/wiki/List_of_Lebanese_architects?action=edit",
                                "goal": (
                                    "Click editor, insert '* [[Nazih Dergham]] (born 1945)' into list, "
                                    "click Show preview."
                                ),
                                "extracted_profile": {"architect": "Nazih Dergham"},
                                "questions_for_user": [],
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setenv("PLANNER_API_KEY", "test-key")
    monkeypatch.setattr("jev_ultrafast.chat_agent.post_json", mock_post)

    msg = "CAN U ADD TO THIS TAB FORMATTED PROPERLY A NEW ARCHITECT : NAZIH DERGHAM BORN 1945"
    history = [{"role": "user", "content": msg}]
    current_url = "https://en.wikipedia.org/wiki/List_of_Lebanese_architects?action=edit"
    res = chat_turn(history, {}, current_url=current_url)

    assert res["action_required"] is True
    assert len(calls) == 1
    sent_messages = calls[0]["messages"]
    system_prompt = sent_messages[0]["content"]
    user_prompt = sent_messages[1]["content"][0]["text"]

    # Verify system prompt has instructions for CURRENT ACTIVE TAB and PAGE EDITING
    assert "CURRENT ACTIVE TAB: <url>" in system_prompt
    assert "PAGE EDITING & INSERTING ENTRIES" in system_prompt
    assert "NEVER claim the URL is missing" in system_prompt

    # Verify user message explicitly and prominently features CURRENT ACTIVE TAB
    assert f"CURRENT ACTIVE TAB: {current_url}" in user_prompt
    assert "If the user refers to 'this tab', 'this page', 'here'" in user_prompt
    assert "insert an entry" in user_prompt


def test_chat_turn_handles_none_current_url_gracefully(monkeypatch):
    from jev_ultrafast.chat_agent import chat_turn

    calls = []

    def mock_post(_url, _key, body):
        calls.append(body)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "reply": "Which page would you like me to open?",
                                "action_required": False,
                                "start_url": None,
                                "goal": "",
                                "extracted_profile": {},
                                "questions_for_user": ["Which URL or page should I edit?"],
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setenv("PLANNER_API_KEY", "test-key")
    monkeypatch.setattr("jev_ultrafast.chat_agent.post_json", mock_post)

    chat_turn([{"role": "user", "content": "hello"}], {}, current_url=None)
    assert len(calls) == 1
    user_prompt = calls[0]["messages"][1]["content"][0]["text"]
    assert "CURRENT ACTIVE TAB: None (blank/new tab)" in user_prompt


def test_bridge_fresh_session_and_reset(tmp_path, monkeypatch):
    import jev_ultrafast.bridge as bridge_mod
    from jev_ultrafast.bridge import AssistBridge

    # Point HISTORY_DIR to temporary directory
    history_dir = tmp_path / "history"
    monkeypatch.setattr(bridge_mod, "HISTORY_DIR", history_dir)

    # 1. Fresh bridge initialization starts with clean empty session
    bridge = AssistBridge()
    assert bridge.messages == []
    assert bridge.status == "idle"
    initial_id = bridge.session_id
    assert initial_id.startswith("sess_")

    # 2. Add message to bridge
    bridge.messages.append({"id": 1, "role": "user", "content": "First conversation"})
    bridge._save_session()
    assert (history_dir / f"{initial_id}.json").exists()

    # 3. New session starts completely clean
    new_state = bridge.new_session()
    assert new_state["messages"] == []
    assert new_state["status"] == "idle"
    assert new_state["id"] != initial_id
    assert bridge.session_id != initial_id

    # 4. Old session is archived and loadable from history
    loaded_state = bridge.load_session(initial_id)
    assert len(loaded_state["messages"]) == 1
    assert loaded_state["messages"][0]["content"] == "First conversation"
    assert bridge.session_id == initial_id

    # 5. Reset again gives clean session
    reset_state = bridge.reset()
    assert reset_state["messages"] == []
    assert bridge.messages == []


def test_bridge_worker_generation_invalidation(tmp_path, monkeypatch):
    import jev_ultrafast.bridge as bridge_mod
    from jev_ultrafast.bridge import AssistBridge

    history_dir = tmp_path / "history"
    monkeypatch.setattr(bridge_mod, "HISTORY_DIR", history_dir)

    bridge = AssistBridge()
    initial_gen = bridge.generation

    # Calling stop or new_session increments generation
    bridge.stop()
    assert bridge.generation == initial_gen + 1

    bridge.new_session()
    assert bridge.generation == initial_gen + 3


def test_bridge_updates_current_url_from_explicit_message(tmp_path, monkeypatch):
    import jev_ultrafast.bridge as bridge_mod
    from jev_ultrafast.bridge import AssistBridge

    history_dir = tmp_path / "history"
    monkeypatch.setattr(bridge_mod, "HISTORY_DIR", history_dir)

    bridge = AssistBridge()
    bridge.current_url = "https://initial.example.com"

    recorded = []

    def mock_chat_turn(history, prof, current_url=None, files=()):
        recorded.append(current_url)
        return {"reply": "ok", "action_required": False, "goal": "", "questions_for_user": []}

    monkeypatch.setattr("jev_ultrafast.bridge.chat_turn", mock_chat_turn)
    monkeypatch.setattr("jev_ultrafast.planner.ensure_local_server", lambda: None)
    monkeypatch.setattr("jev_ultrafast.relay.start", lambda: None)

    explicit_url = "https://en.wikipedia.org/w/index.php?title=List_of_Lebanese_architects&action=edit"
    bridge.send_message(f"{explicit_url} this page here")

    if bridge.worker_thread:
        bridge.worker_thread.join(timeout=2.0)

    assert len(recorded) == 1
    assert recorded[0] == explicit_url
    assert bridge.current_url == explicit_url


def teacher_page():
    p = page()
    for a in p["actions"]:
        if "node" in a:
            a["rect"] = {"x": a["node"], "y": 5, "w": 40, "h": 20}
    p.update(w=1200, h=800, screenshot=base64.b64encode(b"jpeg-bytes").decode())
    return p


def teacher_post(calls):
    def post(_url, _key, body, **kwargs):
        calls.append((body, kwargs))
        ops = body["questions"]["operation"]["criteria"]
        return {"model": "jev", "answers": {"operation": choice(ops, "CLICK"), "click_target": choice(["1", "2"], "2")}}

    return post


def test_teacher_log_saves_screenshot_rects_viewport_and_gear(monkeypatch, tmp_path):
    log = tmp_path / "task.jsonl"
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("JEV_LOG", str(log))
    monkeypatch.delenv("VISION_TEACHER", raising=False)
    monkeypatch.setattr(model, "post_json", teacher_post([]))
    model.choose(teacher_page(), "Find a book", [])
    line = json.loads(log.read_text(encoding="utf-8"))
    assert line["gear"] == 1 and line["model"] == "jev" and "click_target" in line["answers"]
    assert line["viewport"] == {"w": 1200, "h": 800}
    assert line["rects"] == {"1": {"x": 10, "y": 5, "w": 40, "h": 20}, "2": {"x": 20, "y": 5, "w": 40, "h": 20}}
    assert line["image"].startswith("img/") and (tmp_path / line["image"]).read_bytes() == b"jpeg-bytes"
    assert "rect" not in json.dumps(line["request"]["state"]["elements"])


def test_vision_teacher_off_leaves_request_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.delenv("VISION_TEACHER", raising=False)
    monkeypatch.delenv("JEV_LOG", raising=False)
    plain, logged = [], []
    monkeypatch.setattr(model, "post_json", teacher_post(plain))
    model.choose(teacher_page(), "Find a book", [])
    monkeypatch.setenv("JEV_LOG", str(tmp_path / "task.jsonl"))
    monkeypatch.setattr(model, "post_json", teacher_post(logged))
    model.choose(teacher_page(), "Find a book", [])
    assert plain == logged and plain[0][1] == {}


def test_vision_teacher_sends_screen_asset_header_and_box_question(monkeypatch, tmp_path):
    log = tmp_path / "task.jsonl"
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("JEV_LOG", str(log))
    monkeypatch.setenv("VISION_TEACHER", "1")
    calls = []
    monkeypatch.setattr(model, "post_json", teacher_post(calls))
    p = teacher_page()
    d = model.choose(p, "Find a book", [])
    body, kwargs = calls[0]
    screen = body["state"]["screen"]
    assert screen.endswith("-screen")
    assert body["assets"] == {screen: {"type": "image", "data_base64": p["screenshot"], "format": "jpeg"}}
    assert kwargs == {"headers": {"TypeSafe-Danger-Zone-Images": "true"}}
    assert body["questions"]["vision_box"]["type"] == "box"
    assert "Find a book" in body["questions"]["vision_box"]["instructions"]
    assert d["choice"] == "e3"
    line = json.loads(log.read_text(encoding="utf-8"))
    assert "assets" not in line["request"] and line["image"] == f"img/{screen[: -len('-screen')]}.jpg"


def test_gear2_decision_is_logged(monkeypatch, tmp_path):
    log = tmp_path / "task.jsonl"
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("JEV_LOG", str(log))
    reply = '{"operation": "CLICK", "target": "2", "reason": "go"}'
    monkeypatch.setattr(model, "post_json", lambda *_: {"choices": [{"message": {"content": reply}}]})
    p = teacher_page()
    d = model.choose_vision(p, "Find a book", [], p["screenshot"])
    line = json.loads(log.read_text(encoding="utf-8"))
    assert d["choice"] == "e3" and line["gear"] == 2 and line["output"]["target"] == "2"
    assert line["rects"]["2"]["x"] == 20 and (tmp_path / line["image"]).exists()


def test_agent_screenshots_only_when_teacher_logging(runner, monkeypatch):
    runner.state["status"] = "ready"
    runner.state["browser"].call = Mock(return_value={"data": "c2hvdA=="})
    monkeypatch.setenv("GEAR2", "0")
    monkeypatch.setattr(loop, "choose", Mock(return_value=decision()))
    monkeypatch.delenv("JEV_LOG", raising=False)
    runner.command("predict")
    runner.state["browser"].call.assert_not_called()
    monkeypatch.setenv("JEV_LOG", "unused.jsonl")
    runner.command("predict")
    assert runner.state["page"]["screenshot"] == "c2hvdA=="


def click(action="e3"):
    return {**decision(action), "operation": "CLICK"}


def test_no_progress_loop_stops_with_reason(runner):
    for _ in range(3):
        runner.state["decision"] = {**click(), "gear": 2}
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    stop = runner.state["stop_reason"]
    assert runner.state["status"] == "blocked" and stop["code"] == "loop"
    assert "Go" in stop["message"] and stop["steps"] == 3 and stop["url"] == "https://example.test/"


def test_blocked_choice_explains_which_gears_gave_up(runner):
    blocked = {"choice": "BLOCKED", "operation": "BLOCKED", "gear": 2, "reason": "captcha"}
    runner.state["decision"] = {**decision(), **blocked}
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    stop = runner.state["stop_reason"]
    assert stop["code"] == "model_blocked" and "vision fallback" in stop["message"]
    assert stop["model_reason"] == "captcha"


def test_step_limit_is_a_reasoned_stop_not_a_crash(runner):
    runner.state["history"] = [{"page_changed": True, "kind": "click", "action": "x"}] * loop.MAX_STEPS
    runner.state["decision"] = click()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["stop_reason"]["code"] == "step_limit"
    runner.state["browser"].act.assert_not_called()


def test_crash_during_run_becomes_a_stop_reason(runner, monkeypatch, tmp_path):
    log = tmp_path / "stops.jsonl"
    monkeypatch.setenv("JEV_STOP_LOG", str(log))
    monkeypatch.setenv("GEAR2", "0")
    runner.state["status"] = "ready"
    monkeypatch.setattr(loop, "choose", Mock(side_effect=ConnectionError("gateway down")))
    states = list(runner.run())
    assert states[-1]["stop_reason"]["code"] == "error" and "gateway down" in states[-1]["stop_reason"]["message"]
    assert json.loads(log.read_text(encoding="utf-8"))["code"] == "error"


def test_missing_value_still_reaches_the_caller(runner, monkeypatch):
    monkeypatch.setenv("GEAR2", "0")
    runner.state["status"] = "ready"
    monkeypatch.setattr(loop, "choose", Mock(return_value=decision()))
    monkeypatch.setattr(loop, "field_text", Mock(side_effect=model.MissingValue("Email")))
    with pytest.raises(model.MissingValue):
        list(runner.run())


def empty_page():
    state = {"url": "https://shop.test/", "title": "", "text": "", "scroll": {"y": 0},
             "actions": [{"id": "wait", "kind": "wait", "label": "Wait"}]}
    state["fingerprint"] = fingerprint(state)
    return state


def test_empty_page_waits_instead_of_asking_models(runner, monkeypatch):
    runner.state.update(status="ready", page=empty_page())
    runner.state["browser"].observe.return_value = empty_page()
    chooser = Mock()
    monkeypatch.setattr(loop, "choose", chooser)
    runner.command("predict")
    chooser.assert_not_called()
    assert runner.state["decision"]["choice"] == "wait"


def test_page_that_never_renders_stops_as_empty_page(runner, monkeypatch):
    monkeypatch.setattr(loop, "choose", Mock())
    runner.state.update(status="ready", page=empty_page())
    runner.state["browser"].observe.return_value = empty_page()
    for _ in range(8):
        runner.command("tick")
    assert runner.state["stop_reason"]["code"] == "empty_page"


def test_observe_retries_while_page_is_blank(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.session, b.allow_upload = "s", False
    b.evaluate = Mock(return_value=False)
    b.settle = Mock()
    monkeypatch.setattr(browser, "browser_operation", Mock(side_effect=[empty_page(), empty_page(), page()]))
    assert b.observe(screenshot=False)["text"] == "Search"
    assert b.settle.call_count == 2


def test_wait_is_always_fresh_and_fill_checks_only_its_node():
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    p = {**page(), "page_key": "k", "guards": {"10": "g"}, "marker": "old"}
    b.evaluate = Mock(return_value=["k", "g"])
    assert b.fresh(p, {"kind": "wait"}) is True
    assert b.fresh(p, {"kind": "fill", "node": 10}) is True  # marker changed elsewhere; node is unchanged
    b.evaluate.return_value = ["k", "changed"]
    assert b.fresh(p, {"kind": "fill", "node": 10}) is False


def test_local_laya_port_follows_env(monkeypatch):
    from jev_ultrafast import planner

    monkeypatch.setenv("TYPESAFE_BASE_URL", "http://127.0.0.1:8793")
    assert planner.local_laya_port() == 8793
    monkeypatch.delenv("TYPESAFE_BASE_URL")
    monkeypatch.setenv("LAYA_PORT", "8800")
    assert planner.local_laya_port() == 8800


@pytest.mark.parametrize(
    "label, gated",
    [
        ("Submit search", False),
        ("Dismiss sign-in info.", False),
        ("Sign in", True),
        ("Submit application", True),
        ("Cancel order", True),
        ("Place order", True),
        ("Search", False),
    ],
)
def test_safety_gate_skips_harmless_clicks_only(label, gated):
    assert loop.sensitive(label) is gated


def test_evidence_must_be_on_the_page():
    from jev_ultrafast.contracts import evidence_holds

    text = "100 EUR =\n 108.43  USD   Mid-market rate"
    assert evidence_holds(["100 EUR = 108.43 USD"], text)
    assert not evidence_holds(["100 EUR = 109.00 USD"], text)
    assert not evidence_holds([], text) and not evidence_holds(None, text)


def test_planner_rejects_done_without_visible_evidence(monkeypatch):
    from jev_ultrafast import planner

    monkeypatch.setattr(planner, "ask", Mock(return_value={"done": True, "evidence": ["Banana bread recipe"]}))
    bread = {**page(), "text": "Bread category"}
    assert planner.verify("Open a banana bread recipe", bread)["done"] is False
    planner.ask.return_value = {"done": True, "evidence": ["Bread category"]}
    assert planner.verify("Open the bread category", bread)["done"] is True


def test_planner_stops_early_once_goal_is_visible(monkeypatch):
    from jev_ultrafast import planner

    agent = loop.Agent.__new__(loop.Agent)
    target = {**page(), "url": "https://example.test/book", "text": "The Book page"}
    agent.state = {"status": "ready", "page": page(), "history": [], "goal": "Open the book"}
    agent.state.update(started_at=None, elapsed_ms=0)

    def run():
        agent.state["history"].append({"action": "Go", "page_changed": True})
        agent.state["page"] = target
        yield agent.snapshot()
        raise AssertionError("kept running after the goal was verified")

    agent.run = run
    agent.snapshot = lambda: dict(agent.state)
    agent.__enter__ = lambda *_: agent
    ctx = Mock(__enter__=Mock(return_value=agent), __exit__=Mock(return_value=False))
    monkeypatch.setattr(planner, "Agent", lambda *a, **k: ctx)
    monkeypatch.setattr(planner, "ensure_local_server", lambda: None)
    replies = [{"url": "https://example.test/", "goal": "Open the book", "profile": {}},
               {"done": False, "evidence": [], "missing": "not yet"},  # arrival check on the start page
               {"done": True, "evidence": ["The Book page"], "missing": ""}]
    monkeypatch.setattr(planner, "ask", Mock(side_effect=replies))
    result = planner.run("Open the book", report=lambda _: None)
    assert result["done"] is True and agent.state["stop_reason"]["code"] == "verified_done"
    assert planner.ask.call_count == 3  # plan, arrival, early; no second verification after the early one


def test_outcome_check_needs_quoted_proof(monkeypatch):
    from jev_ultrafast import chat_agent

    reply = '{"completed": true, "summary": "Converted", "evidence": ["108.43 USD"]}'
    monkeypatch.setattr(chat_agent, "post_json", lambda *_: {"choices": [{"message": {"content": reply}}]})
    unconverted = {**page(), "text": "Currency converter"}
    assert chat_agent.synthesize_outcome("Convert 100 EUR", unconverted)["completed"] is False
    assert chat_agent.synthesize_outcome("Convert 100 EUR", {**page(), "text": "= 108.43 USD"})["completed"] is True


def test_evidence_matches_title_and_ignores_punctuation():
    from jev_ultrafast.contracts import evidence_holds

    text = "Filter by\nRepositories 1.2k\nmicrosoft/playwright"
    title = "Repositories search results - playwright"
    assert evidence_holds(["repositories Search Results · playwright"], text, title)
    assert evidence_holds(["microsoft/playwright ... Repositories"], text)
    assert not evidence_holds(["London, England"], text, "Berlin-Mitte Weather")


def test_planner_checks_goal_before_calling_a_loop_a_failure(monkeypatch):
    from jev_ultrafast import planner

    agent = loop.Agent.__new__(loop.Agent)
    done_page = {**page(), "url": "https://maps.test/dir", "text": "Route: 12 min via A1"}
    agent.state = {"status": "blocked", "page": done_page, "history": [], "goal": "Get directions"}
    agent.state.update(started_at=None, elapsed_ms=0, stop_reason={"code": "call_budget", "message": "budget"})
    agent.run = lambda: iter(())
    ctx = Mock(__enter__=Mock(return_value=agent), __exit__=Mock(return_value=False))
    monkeypatch.setattr(planner, "Agent", lambda *a, **k: ctx)
    monkeypatch.setattr(planner, "ensure_local_server", lambda: None)
    replies = [{"url": "https://maps.test/", "goal": "Get directions", "profile": {}},
               {"done": False, "evidence": [], "missing": "not yet"},  # arrival check
               {"done": True, "evidence": ["Route: 12 min"], "missing": ""}]
    monkeypatch.setattr(planner, "ask", Mock(side_effect=replies))
    result = planner.run("Get directions", report=lambda _: None)
    assert result["done"] is True and agent.state["stop_reason"]["after"] == "call_budget"


def test_planner_never_overrules_a_refusal(monkeypatch):
    from jev_ultrafast import planner

    fake = Mock(state={"status": "blocked", "page": page(), "stop_reason": {"code": "error", "message": "crash"}})
    fake.snapshot = Mock(return_value={"page": page()})
    fake.__enter__ = Mock(return_value=fake)
    fake.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(planner, "Agent", Mock(return_value=fake))
    monkeypatch.setattr(planner, "ensure_local_server", lambda: None)
    ask = Mock(return_value={"url": "https://example.test/", "goal": "Find a book", "profile": {}})
    monkeypatch.setattr(planner, "ask", ask)
    assert planner.run("Find a book", report=lambda _: None)["done"] is False and ask.call_count == 2  # plan, arrival


def test_target_that_did_nothing_twice_is_hidden(runner):
    for _ in range(2):
        runner.state["history"].append(
            {"action": "Go", "kind": "click", "page_changed": False, "url": runner.state["page"]["url"]}
        )
    seen = runner.without_dead_ends(runner.state["page"])
    assert "Go" not in {a["label"] for a in seen["actions"]} and seen["dead_ends"] == ["Go"]
    assert any(a["label"] == "Go" for a in runner.state["page"]["actions"])  # the real page is untouched


def test_goal_values_ignore_apostrophes_and_dates():
    from jev_ultrafast.contracts import goal_values

    goal = "Find the user's flight from 'Berlin' on '2027-01-15', don't book. Check 'Paris'"
    assert goal_values(goal) == ["Berlin", "Paris"]


def test_route_without_the_destination_is_not_done(monkeypatch):
    from jev_ultrafast import planner

    goal = "Get directions from 'Eiffel Tower, Paris' to 'Louvre Museum, Paris'"
    route = {**page(), "url": "https://maps.test/dir/Eiffelturm/48.85,2.29/", "text": "Eiffelturm 6 min 400 m Paris"}
    monkeypatch.setattr(planner, "ask", Mock(return_value={"done": True, "evidence": ["Eiffelturm", "6 min 400 m"]}))
    check = planner.verify(goal, route)
    assert check["done"] is False and "Louvre" in check["missing"]
    route["text"] += " Louvre Museum 25 min"
    assert planner.verify(goal, route)["done"] is True


def test_typed_field_values_count_as_on_the_page():
    from jev_ultrafast.contracts import missing_goal_values

    form = {**page(), "text": "Student Registration Form First Name"}
    form["actions"] = [{**page()["actions"][0], "value": "Test"}]
    assert missing_goal_values("Fill First Name 'Test'", form) == []


def stuck_agent(code="loop"):
    agent = loop.Agent.__new__(loop.Agent)
    agent.screenshots = False
    agent.pending_text = None
    browser = Mock(observe=Mock(return_value=page()))
    agent.state = {"status": "blocked", "page": page(), "history": [{"action": "Go"}], "decisions": [{}],
                   "goal": "Find a book", "browser": browser, "started_at": None, "elapsed_ms": 0,
                   "stop_reason": {"code": code, "message": "Stuck"}}
    agent.snapshot = lambda: dict(agent.state)
    return agent


def test_restart_keeps_the_failed_attempt_and_resets_budgets():
    agent = stuck_agent()
    agent.restart("Find a book via search URL", "https://example.test/search?q=book")
    agent.state["browser"].call.assert_called_once_with("Page.navigate", url="https://example.test/search?q=book")
    assert agent.state["status"] == "ready" and agent.state["history"] == [] and agent.state["decisions"] == []
    assert agent.state["attempts"][0]["stop"] == "loop" and agent.state["attempts"][0]["last_actions"] == ["Go"]


def test_recovery_keeps_the_original_goal_and_rejects_bad_urls():
    from jev_ultrafast.recovery import plan_recovery

    reply = {"action": "retry", "url": "javascript:alert(1)", "goal": "Type the date", "why": "picker loops"}
    plan = plan_recovery("Pick 15 March 2027", stuck_agent(), ask=lambda *_: reply)
    assert plan["url"] is None and plan["goal"].startswith("Pick 15 March 2027") and "Type the date" in plan["goal"]
    assert plan_recovery("x", stuck_agent(), ask=lambda *_: "nonsense")["action"] == "give_up"


def test_planner_retries_a_stuck_run_on_a_new_route(monkeypatch):
    from jev_ultrafast import planner

    agent = stuck_agent()
    agent.state["status"] = "ready"
    runs = iter([[("blocked", {"code": "loop", "message": "Stuck"})], [("done", {"code": "done", "message": "ok"})]])

    def run():
        for status, stop in next(runs):
            agent.state.update(status=status, stop_reason=stop)
            yield agent.snapshot()

    agent.run = run
    ctx = Mock(__enter__=Mock(return_value=agent), __exit__=Mock(return_value=False))
    monkeypatch.setattr(planner, "Agent", lambda *a, **k: ctx)
    monkeypatch.setattr(planner, "ensure_local_server", lambda: None)
    replies = [
        {"url": "https://example.test/", "goal": "Find a book", "profile": {}},
        {"done": False, "evidence": [], "missing": "not yet"},  # arrival check
        {"done": False, "evidence": [], "missing": "no book"},  # verify after the loop
        {"action": "retry", "url": "https://example.test/search?q=book", "goal": "Use search", "why": "loop"},
        {"done": False, "evidence": [], "missing": "not yet"},  # arrival check on the recovery URL
        {"done": True, "evidence": ["Search"], "missing": ""},  # final verify on the new route
    ]
    monkeypatch.setattr(planner, "ask", Mock(side_effect=replies))
    result = planner.run("Find a book", report=lambda _: None)
    assert result["done"] is True and agent.state["attempts"][0]["stop"] == "loop"


def test_every_requirement_must_be_met_and_quoted(monkeypatch):
    from jev_ultrafast import planner

    xe = {**page(), "url": "https://xe.test/convert/?Amount=100&From=EUR&To=INR", "text": "100.00 EUR = 9,712 INR"}
    reply = {
        "done": True,
        "requirements": [
            {"need": "amount 100", "met": True, "quote": "100.00 EUR"},
            {"need": "from USD", "met": False, "quote": ""},
            {"need": "to EUR", "met": True, "quote": "EUR"},
        ],
    }
    monkeypatch.setattr(planner, "ask", Mock(return_value=reply))
    check = planner.verify("Convert 100 US Dollars to Euros", xe)
    assert check["done"] is False and "from USD" in check["missing"]


def test_session_ids_never_repeat():
    from jev_ultrafast.bridge import new_session_id

    ids = {new_session_id() for _ in range(50)}
    assert len(ids) == 50
