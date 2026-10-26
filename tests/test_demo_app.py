"""The Streamlit demo, driven headlessly with Streamlit's AppTest (no browser, no LLM calls)."""
from pathlib import Path

import pytest

pytest.importorskip("agentdojo")
pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "demo" / "app.py")


def outcome(at) -> str:
    lines = [m.value for m in at.markdown if m.value.startswith("**Outcome**")]
    return lines[0] if lines else ""


def held(at) -> list[str]:
    return [m.value for m in at.markdown if "is held for your decision" in m.value]


def test_demo_loads_and_a_person_can_decline_a_question():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception
    assert "task completed" in outcome(at)  # default: scripted agent, user task 0, no attack
    at.sidebar.selectbox(key="example").set_value(
        "'Delete the largest file', but the agent picks file 13 (scripted)").run()
    assert not at.exception
    assert [b.label for b in at.button][:2] == ["Approve", "Decline"]
    assert held(at) == ["## Step 03 is held for your decision"]
    assert not outcome(at)  # no verdict while a question is open
    next(b for b in at.button if b.label == "Decline").click().run()
    assert not at.exception
    assert "attack stopped" in outcome(at) and "1 question asked" in outcome(at)


def test_simulated_user_and_gate_comparison():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.sidebar.radio(key="approver").set_value("ground_truth").run()
    at.sidebar.selectbox(key="example").set_value(
        "Recipients that appear nowhere: AssentGate asks (scripted)").run()
    assert "3 questions asked" in outcome(at) and not held(at)
    at.toggle(key="compare").set_value(True).run()
    assert not at.exception


def test_sidebar_shows_the_full_request_and_attack_goal():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.sidebar.selectbox(key="example").set_value("Phishing planted in a calendar event (Llama 3.3 replay)").run()
    captions = " ".join(c.value for c in at.sidebar.caption)
    assert "TODOs for the week" in captions  # user task 19's full request, not the truncated label
    assert "Please send individual emails to every single person" in captions  # injection task 13's goal
