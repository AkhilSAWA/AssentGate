"""The Streamlit demo, driven headlessly with Streamlit's AppTest (no browser, no LLM calls)."""
from pathlib import Path

import pytest

pytest.importorskip("agentdojo")
pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "demo" / "app.py")


def metrics(at):
    return {m.label: m.value for m in at.metric}


def test_demo_loads_and_a_person_can_decline_a_question():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception
    assert metrics(at)["Task completed"] == "yes"  # default: scripted agent, user task 0, no attack
    at.sidebar.selectbox(key="example").set_value(
        "'Delete the largest file', but the agent picks file 13 (scripted)").run()
    assert not at.exception
    assert [b.label for b in at.button][:2] == ["Approve", "Decline"]
    assert any("AssentGate asks before step 3" in m.value for m in at.markdown)
    decline = next(b for b in at.button if b.label == "Decline")
    decline.click().run()
    assert not at.exception
    assert metrics(at)["Attack succeeded"] == "no" and metrics(at)["Questions asked"] == "1"


def test_simulated_user_and_gate_comparison():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.sidebar.radio(key="approver").set_value("ground_truth").run()
    at.sidebar.selectbox(key="example").set_value(
        "Recipients that appear nowhere: AssentGate asks (scripted)").run()
    assert metrics(at)["Questions asked"] == "3"
    at.toggle(key="compare").set_value(True).run()
    assert not at.exception
