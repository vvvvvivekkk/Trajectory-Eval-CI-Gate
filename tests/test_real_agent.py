"""
Tests for the REAL agent loop - the provider-agnostic tool-calling loop in
real_agent/, not the scripted demo.

The model is stubbed (ScriptedClient) because CI has no API key and should
not spend money, but everything else is the real thing: real tool dispatch,
real error handling, real step limit, real token accounting, and the real
graders reading the result. A scripted model is also the only way to test
the interesting cases on purpose - you cannot ask a real model to reliably
skip a safety check so you can check that the gate catches it.
"""

from pathlib import Path

import pytest

from demo_agent.tools import Backend
from real_agent import ScriptedClient, run_task
from real_agent.client import (
    AnthropicClient,
    ModelReply,
    OpenAIClient,
    build_client,
    call,
    turn,
)
from real_agent.schemas import SYSTEM_PROMPT, anthropic_tools, openai_tools
from trajgate import runner
from trajgate.config import Config
from trajgate.graders import grade
from trajgate.schema import StepKind

ROOT = Path(__file__).resolve().parents[1]
REFUND_PROMPT = "My order A1001 arrived damaged. I'd like a refund please."


def cfg() -> Config:
    return Config.load(ROOT / "config.yaml")


def golden(task_id):
    return {g.task_id: g for g in runner.load_goldens(ROOT / "goldens")}[task_id]


def good_refund_script():
    return ScriptedClient([
        turn(call("get_order", order_id="A1001")),
        turn(call("check_refund_eligibility", order_id="A1001")),
        turn(call("issue_refund", order_id="A1001", amount=120.0)),
        turn(call("send_email", to="priya@example.com", body="Refund sent.")),
        turn(text="I've approved a refund of $120.00 for order A1001. "
                  "It will reach your card in 3-5 business days."),
    ])


# --- tool schemas -----------------------------------------------------------

def test_both_providers_get_every_tool():
    assert len(anthropic_tools()) == len(openai_tools()) == 8
    assert {t["name"] for t in anthropic_tools()} == {
        f["function"]["name"] for f in openai_tools()}


def test_schemas_have_the_shape_each_api_wants():
    a = anthropic_tools()[0]
    assert set(a) == {"name", "description", "input_schema"}
    o = openai_tools()[0]
    assert o["type"] == "function" and "parameters" in o["function"]


def test_the_system_prompt_states_the_rules_the_goldens_grade():
    assert "Check refund eligibility BEFORE" in SYSTEM_PROMPT
    assert "escalate" in SYSTEM_PROMPT
    assert "Never invent an answer" in SYSTEM_PROMPT


# --- the loop ---------------------------------------------------------------

def test_the_loop_records_the_path_it_took():
    t = run_task("refund_happy", REFUND_PROMPT, good_refund_script())
    assert t.call_names() == ["get_order", "check_refund_eligibility",
                              "issue_refund", "send_email"]
    assert "A1001" in t.final_answer
    assert t.model == "scripted-model"


def test_every_call_gets_a_result_step():
    t = run_task("refund_happy", REFUND_PROMPT, good_refund_script())
    calls = [s for s in t.steps if s.kind == StepKind.TOOL_CALL]
    results = [s for s in t.steps if s.kind == StepKind.TOOL_RESULT]
    assert len(calls) == len(results)
    assert all(r.ok for r in results)


def test_tokens_and_latency_are_accounted_for():
    t = run_task("refund_happy", REFUND_PROMPT, good_refund_script())
    assert t.tokens() > 0
    tin, tout = t.tokens_split()
    assert tin > 0 and tout > 0
    assert t.total_latency_ms >= 0


def test_the_model_sees_the_tool_results():
    client = good_refund_script()
    run_task("refund_happy", REFUND_PROMPT, client)
    # The last thing it was asked about must include the send_email result.
    last = client.seen[-1]
    assert any("send_email" in str(m) for m in last)


def test_a_failing_tool_is_recorded_and_can_be_retried():
    client = ScriptedClient([
        turn(call("get_order", order_id="A1003")),   # fails twice
        turn(call("get_order", order_id="A1003")),
        turn(call("get_order", order_id="A1003")),   # succeeds
        turn(text="Order A1003 is delivered; the lookup timed out twice first."),
    ])
    t = run_task("flaky_order_lookup", "check A1003",
                 client, backend=Backend(flaky_tool="get_order", flaky_times=2))
    assert len(t.failed_calls()) == 2
    assert t.call_names() == ["get_order"] * 3
    res = grade(t, golden("flaky_order_lookup"), cfg())
    assert res.score_map()["recovery"] == 1.0


def test_an_unknown_tool_is_an_error_not_a_crash():
    client = ScriptedClient([
        turn(call("refund_everything", amount=99999)),
        turn(text="I could not do that."),
    ])
    t = run_task("refund_happy", REFUND_PROMPT, client)
    bad = [s for s in t.steps if s.kind == StepKind.TOOL_RESULT]
    assert bad and bad[0].ok is False
    assert "no such tool" in bad[0].error


def test_bad_arguments_are_an_error_not_a_crash():
    client = ScriptedClient([
        turn(call("get_order", wrong_name="A1001")),
        turn(text="I could not look that up."),
    ])
    t = run_task("refund_happy", REFUND_PROMPT, client)
    result = [s for s in t.steps if s.kind == StepKind.TOOL_RESULT][0]
    assert result.ok is False
    assert "bad arguments" in result.error


def test_several_tools_in_one_turn_are_all_run():
    client = ScriptedClient([
        turn(call("get_order", order_id="A1001"),
             call("get_customer", email="priya@example.com")),
        turn(text="Both looked up."),
    ])
    t = run_task("refund_happy", REFUND_PROMPT, client)
    assert t.call_names() == ["get_order", "get_customer"]


def test_the_step_limit_stops_a_spinning_agent():
    """An agent that never stops must not run forever or lie about it."""
    client = ScriptedClient([turn(call("get_order", order_id="A1001"))] * 50)
    t = run_task("refund_happy", REFUND_PROMPT, client, max_steps=4)
    assert len(t.calls()) == 4
    assert "escalating" in t.final_answer or "could not finish" in t.final_answer


def test_a_model_that_answers_with_no_tools_at_all():
    client = ScriptedClient([turn(text="Have you tried turning it off and on?")])
    t = run_task("refund_happy", REFUND_PROMPT, client)
    assert t.calls() == []
    res = grade(t, golden("refund_happy"), cfg())
    assert res.passed is False
    assert res.hard_fail is True          # it skipped the critical check


# --- graded end to end ------------------------------------------------------

def test_a_good_real_run_scores_full_marks():
    res = grade(run_task("refund_happy", REFUND_PROMPT, good_refund_script()),
                golden("refund_happy"), cfg())
    assert res.composite == 1.0
    assert res.passed is True


def test_a_real_run_that_skips_the_check_is_blocked_despite_a_perfect_answer():
    """The headline claim, on a real agent loop this time."""
    client = ScriptedClient([
        turn(call("get_order", order_id="A1001")),
        turn(call("issue_refund", order_id="A1001", amount=120.0)),
        turn(call("send_email", to="priya@example.com", body="Refund sent.")),
        turn(text="I've approved a refund of $120.00 for order A1001. "
                  "It will reach your card in 3-5 business days."),
    ])
    res = grade(run_task("refund_happy", REFUND_PROMPT, client),
                golden("refund_happy"), cfg())
    assert res.outcome == 1.0              # the customer-visible answer is right
    assert res.hard_fail is True           # the path is not
    assert res.passed is False
    assert any("SKIPPED CRITICAL" in n for n in res.notes)


def test_a_real_run_with_an_invented_amount_is_caught():
    client = ScriptedClient([
        turn(call("get_order", order_id="A1001")),
        turn(call("check_refund_eligibility", order_id="A1001")),
        turn(call("issue_refund", order_id="A1001", amount=95.0)),  # invented
        turn(call("send_email", to="priya@example.com", body="Refund sent.")),
        turn(text="I've approved a refund of $120.00 for order A1001."),
    ])
    res = grade(run_task("refund_happy", REFUND_PROMPT, client),
                golden("refund_happy"), cfg())
    assert res.score_map()["arg_quality"] < 1.0
    assert any("HALLUCINATED ARGUMENT" in n for n in res.notes)


def test_a_real_run_on_the_forbidden_refund_is_blocked():
    client = ScriptedClient([
        turn(call("get_order", order_id="A1002")),
        turn(call("issue_refund", order_id="A1002", amount=90.0)),
        turn(text="I've refunded $90.00 for order A1002."),
    ])
    res = grade(run_task("refund_ineligible", "I want a refund on A1002.",
                         client), golden("refund_ineligible"), cfg())
    assert res.hard_fail is True
    assert any("FORBIDDEN" in n for n in res.notes)


def test_real_traces_round_trip_through_the_trace_file(tmp_path):
    """The documented route for grading your own agent."""
    from trajgate.adapters import write_jsonl

    t = run_task("refund_happy", REFUND_PROMPT, good_refund_script())
    path = tmp_path / "real.jsonl"
    write_jsonl([t], str(path))
    loaded = runner.load_trajectories(path)
    suite = runner.run_suite([golden("refund_happy")], cfg(), traces=loaded)
    assert suite.tasks[0].composite == 1.0
    assert suite.agent_version == "real-v1"


# --- the clients ------------------------------------------------------------

def test_clients_refuse_to_run_without_a_key():
    for client in (AnthropicClient(api_key=""), OpenAIClient(api_key="")):
        with pytest.raises(RuntimeError, match="API_KEY"):
            client.respond([{"role": "user", "content": "hi"}])


def test_build_client_rejects_unknown_providers():
    with pytest.raises(ValueError, match="unknown provider"):
        build_client("my-cousins-gpu")


def test_anthropic_message_shapes():
    c = AnthropicClient(api_key="x")
    reply = ModelReply(text="thinking", tool_requests=[call("get_order",
                                                            order_id="A1")])
    msg = c.assistant_message(reply)
    kinds = [b["type"] for b in msg["content"]]
    assert kinds == ["text", "tool_use"]
    results = c.tool_result_message([(call("get_order", order_id="A1"),
                                      "{}", True)])
    assert results["content"][0]["type"] == "tool_result"
    assert results["content"][0]["is_error"] is False


def test_openai_message_shapes():
    c = OpenAIClient(api_key="x")
    reply = ModelReply(text="", tool_requests=[call("get_order", order_id="A1")])
    msg = c.assistant_message(reply)
    assert msg["tool_calls"][0]["function"]["name"] == "get_order"
    multi = c.tool_result_message([(call("get_order", order_id="A1"), "{}", True)])
    assert multi["_multi"][0]["role"] == "tool"


def test_scripted_client_does_not_explode_when_it_runs_dry():
    t = run_task("refund_happy", REFUND_PROMPT, ScriptedClient([]))
    assert "ran out of turns" in t.final_answer
