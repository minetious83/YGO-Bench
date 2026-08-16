"""`goat_llm_v0_1`: gating, output contract, hidden information and traces.

Every test uses a scripted provider, so the suite never touches a network.
"""

from __future__ import annotations

import pytest

from tests.test_goat_heuristic import _card, _idle, _observation
from ygobench.agents.decision_gate import classify
from ygobench.agents.goat_llm import (
    AGENT_VERSION,
    PROMPT_VERSION,
    GoatLLMAgent,
    LLMDecisionError,
    ModelReply,
    build_prompt,
    parse_reply,
)
from ygobench.agents.semantics import translate
from ygobench.engine.protocol import ActionChoice, DecisionRequest
from ygobench.goat.strategy_state import extract


class ScriptedProvider:
    """Returns canned replies and records what it was asked."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def respond(self, *, system: str, user: str) -> ModelReply:
        self.prompts.append(user)
        text = self.replies.pop(0) if self.replies else '{"action_id": "A0"}'
        return ModelReply(
            text=text, model="scripted", usage={"input_tokens": 10, "output_tokens": 5}
        )


def _request(observation, decision, actions):
    return DecisionRequest(
        player=0,
        observation={**observation, "decision": decision},
        legal_actions=tuple(actions),
        decision_type=decision["responder"],
    )


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_single_legal_action_never_reaches_the_model() -> None:
    decision = {"responder": "select_chain", "cards": []}
    actions = [ActionChoice("select_chain", {"index": None}, "decline chain")]
    provider = ScriptedProvider()
    agent = GoatLLMAgent(provider)

    agent.predict(_request(_observation(), decision, actions))

    assert provider.prompts == []
    assert agent.model_calls == 0
    assert agent.automatic_decisions == 1
    assert agent.last_trace["decision_source"] == "automatic"
    assert agent.last_trace["automatic_reason"] == "only one legal action"


def test_chain_window_with_nothing_to_activate_never_reaches_the_model() -> None:
    """The 94%-of-all-decisions case."""

    decision = {"responder": "select_chain", "cards": []}
    actions = [
        ActionChoice("select_chain", {"index": None}, "decline chain"),
        ActionChoice("select_chain", {"index": None}, "passive"),
    ]
    semantic = translate(decision, tuple(actions))
    verdict = classify(semantic, decision)
    assert not verdict.needs_model
    assert verdict.action.is_decline


def test_zone_choice_is_resolved_without_the_model() -> None:
    decision = {"responder": "select_place", "places": []}
    actions = [
        ActionChoice("select_place", {"places": [{"sequence": 1}]}, "zone 1"),
        ActionChoice("select_place", {"places": [{"sequence": 2}]}, "zone 2"),
    ]
    provider = ScriptedProvider()
    agent = GoatLLMAgent(provider)
    agent.predict(_request(_observation(), decision, actions))

    assert agent.model_calls == 0
    assert "not strategically distinct" in agent.last_trace["automatic_reason"]


def test_two_distinct_options_do_reach_the_model() -> None:
    decision, actions = _idle(("activate", "Pot of Greed"), ("to_end_phase", None))
    provider = ScriptedProvider('{"action_id": "A0", "why": "free cards"}')
    agent = GoatLLMAgent(provider)

    agent.predict(_request(_observation(hand=[_card("Pot of Greed")]), decision, actions))

    assert agent.model_calls == 1
    assert agent.last_trace["decision_source"] == "llm"
    assert agent.last_trace["rationale"] == "free cards"


# --------------------------------------------------------------------------
# Output contract
# --------------------------------------------------------------------------


def test_valid_action_id_is_accepted() -> None:
    assert parse_reply('{"action_id": "A2", "why": "x"}', 4) == (2, "x")
    # Bare ids are tolerated, but only when there is no JSON object to trust.
    assert parse_reply("I choose A1", 4)[0] == 1


def test_nonexistent_action_id_is_rejected() -> None:
    with pytest.raises(LLMDecisionError, match="not one of the 3 available"):
        parse_reply('{"action_id": "A9"}', 3)


def test_malformed_reply_is_rejected_rather_than_guessed() -> None:
    with pytest.raises(LLMDecisionError, match="no action id"):
        parse_reply("I would like to summon Jinzo please", 3)


def test_one_retry_then_an_explicit_failure() -> None:
    decision, actions = _idle(("activate", "Pot of Greed"), ("to_end_phase", None))
    provider = ScriptedProvider("nonsense", '{"action_id": "A1"}')
    agent = GoatLLMAgent(provider, max_retries=1)

    chosen = agent.predict(_request(_observation(), decision, actions))

    assert chosen == actions[1]
    assert agent.retries == 1
    assert agent.malformed == 1
    assert agent.model_calls == 2
    assert "Your previous reply could not be used" in provider.prompts[1]


def test_persistent_malformed_output_fails_loudly() -> None:
    """No silent fallback: an unusable model is an agent failure."""

    decision, actions = _idle(("activate", "Pot of Greed"), ("to_end_phase", None))
    provider = ScriptedProvider("nope", "still nope")
    agent = GoatLLMAgent(provider, max_retries=1)

    with pytest.raises(LLMDecisionError, match="did not name a usable action"):
        agent.predict(_request(_observation(), decision, actions))
    assert agent.malformed == 2


# --------------------------------------------------------------------------
# Hidden information and determinism
# --------------------------------------------------------------------------


def test_prompt_never_contains_hidden_opponent_cards() -> None:
    decision, actions = _idle(("activate", "Pot of Greed"), ("to_end_phase", None))
    base = _observation(hand=[_card("Pot of Greed")])
    leaky = {
        **base,
        "opponent": {
            **base["opponent"],
            "hand": [
                _card("Mirror Force", owner="opponent"),
                _card("Jinzo", 2400, owner="opponent"),
            ],
        },
    }
    semantic = translate(decision, tuple(actions))

    prompt = build_prompt(leaky, extract(leaky), semantic)
    assert "Mirror Force" not in prompt
    assert "Jinzo" not in prompt
    # The count is still available, because the player can see it.
    assert "opponent 3 (unknown cards)" in prompt or "opponent 2 (unknown cards)" in prompt


def test_identical_visible_observations_serialize_identically() -> None:
    decision, actions = _idle(("activate", "Pot of Greed"), ("to_end_phase", None))
    base = _observation(hand=[_card("Pot of Greed")])
    other = {
        **base,
        "opponent": {**base["opponent"], "hand": [_card("Raigeki", owner="opponent")] * 3},
    }
    semantic = translate(decision, tuple(actions))

    assert build_prompt(base, extract(base), semantic) == build_prompt(
        other, extract(other), semantic
    )


def test_prompt_lists_every_action_with_a_stable_id() -> None:
    decision, actions = _idle(
        ("activate", "Heavy Storm"), ("summon", "Sangan"), ("to_end_phase", None)
    )
    semantic = translate(decision, tuple(actions))
    prompt = build_prompt(_observation(), extract(_observation()), semantic)

    for index in range(len(actions)):
        assert f"A{index}:" in prompt
    assert "Heavy Storm" in prompt and "Sangan" in prompt


def test_trace_records_the_provenance_of_a_model_decision() -> None:
    decision, actions = _idle(("activate", "Pot of Greed"), ("to_end_phase", None))
    provider = ScriptedProvider('{"action_id": "A0", "why": "card advantage"}')
    agent = GoatLLMAgent(provider, temperature=0.0, model_id="test-model")

    agent.predict(_request(_observation(), decision, actions))
    trace = agent.last_trace

    assert trace["agent"] == AGENT_VERSION
    assert trace["prompt_version"] == PROMPT_VERSION
    assert trace["decision_source"] == "llm"
    assert trace["action_id"] == "A0"
    assert trace["candidate_action_ids"] == ["A0", "A1"]
    assert trace["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert trace["retries"] == 0
    assert "gate_reason" in trace
    # Only a concise rationale is stored, never hidden reasoning.
    assert trace["rationale"] == "card advantage"


def test_agent_returns_only_offered_actions() -> None:
    decision, actions = _idle(("activate", "Pot of Greed"), ("to_end_phase", None))
    agent = GoatLLMAgent(ScriptedProvider('{"action_id": "A1"}'))
    assert agent.predict(_request(_observation(), decision, actions)) in actions
