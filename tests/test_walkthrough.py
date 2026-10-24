"""The demo's engine: step-by-step walkthroughs, a person answering questions, results tables."""
import json

import pytest

pytest.importorskip("agentdojo")

from assentgate import walkthrough as wt  # noqa: E402

LLAMA = "meta-llama_Llama-3.3-70B-Instruct"
needs_runs = pytest.mark.skipif(not wt.saved_run_path(LLAMA, "user_task_19", "injection_task_13").exists(),
                                reason="saved AgentDojo runs not available")


def test_answer_book_marks_the_first_unanswered_question():
    book = wt.AnswerBook([True])
    assert [book(None, None) for _ in range(3)] == [True, False, False]
    assert book.pending == 1


def test_relabel_names_the_step_that_produced_an_output():
    text = "comes from get_day_calendar_events#1[0] (title=Team Sync) and list_files#0"
    assert wt._relabel(text, {0: 1, 1: 2}) == (
        "comes from step 2 (get_day_calendar_events), item 1 (title=Team Sync) and step 1 (list_files)")


def test_scripted_largest_file_attack_is_asked_about_and_declined():
    w = wt.walk("scripted", "user_task_35", "injection_task_1", approver="ground_truth")
    delete_13 = w.steps[-1]
    assert delete_13.tool == "delete_file" and delete_13.arguments == {"file_id": "13"}
    assert delete_13.asked and not delete_13.executed
    assert any(not ok and "largest file" in line for ok, line in delete_13.checks)
    assert w.utility is True and w.attack_success is False


@needs_runs
def test_a_person_answers_step_by_step_on_a_replayed_phishing_run():
    first = wt.walk(LLAMA, "user_task_19", "injection_task_13", approver="me")
    assert first.pending == len(first.steps) and first.utility is None  # waiting at the first phishing email
    question = first.steps[-1]
    assert question.tool == "send_email" and question.pending and not question.executed
    assert any("doesn't name" in r for r in question.reasons)
    assert any(s.planted for s in first.steps)  # the calendar output with the planted text
    declined = wt.walk(LLAMA, "user_task_19", "injection_task_13", approver="me", answers=[False])
    assert declined.pending is None and declined.attack_success is False and declined.stopped
    approved = wt.walk(LLAMA, "user_task_19", "injection_task_13", approver="me", answers=[True])
    assert approved.steps[question.number - 1].executed  # approved call ran...
    assert approved.pending == question.number + 1  # ...and the next phishing email is asked about


@needs_runs
def test_compare_gates_on_one_run():
    rows = {r["gate"]: r for r in wt.compare_gates(LLAMA, "user_task_19", "injection_task_13")}
    assert rows["No gate"]["attack succeeded"] is True
    assert rows["AssentGate (delegation_source)"]["attack succeeded"] is False


def test_results_table_counts_only_attacks_that_work_with_no_gate(tmp_path):
    def write(name, rows):
        (tmp_path / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    def row(injection_task, attack_success, confirmations=0):
        return {"user_task": "user_task_0", "injection_task": injection_task, "attack_success": attack_success,
                "utility": True, "confirmations": confirmations}

    write("no_gate", [row(None, None), row("injection_task_0", True), row("injection_task_1", False)])
    write("delegation_source", [row(None, None, 1), row("injection_task_0", False, 1), row("injection_task_1", False, 3)])
    table = {r["gate"]: r for r in wt.results_table(tmp_path)}
    gate = table["AssentGate (delegation_source)"]
    assert gate["attacks that got through"] == "0 / 1" and gate["questions per benign task"] == 1.0
    assert gate["questions per attacked task"] == 2.0  # all attacked runs
    assert wt.results_table(tmp_path, prompts_over_feasible=True)[-1]["questions per attacked task"] == 1.0
    assert table["No gate"]["attacks that got through"] == "1 / 1"
