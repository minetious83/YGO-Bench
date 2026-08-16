"""Strategy-agent tests for `goat_heuristic_v1`.

These are about *decision quality*, not legality: the engine still decides what
is available, and each scenario is one where the preferred action is meant to be
obvious.  Scoring is exercised directly against synthetic prompts so a scenario
stays readable, plus end-to-end checks that the agent survives real duels.
"""

from __future__ import annotations

import pytest

from ygobench.agents.goat_heuristic import AGENT_VERSION, GoatHeuristicAgent, score_actions
from ygobench.agents.semantics import translate
from ygobench.engine.protocol import ActionChoice, DecisionRequest
from ygobench.goat.strategy_state import extract


def _card(name, attack=0, owner="you", **extra):
    return {
        "name": name,
        "attack": attack,
        "owner": owner,
        "code": abs(hash(name)) % 10**8,
        **extra,
    }


def _observation(
    *,
    my_monsters=(),
    their_monsters=(),
    my_backrow=(),
    their_backrow=(),
    my_grave=(),
    their_grave=(),
    hand=(),
    my_lp=8000,
    their_lp=8000,
    decision=None,
    phase="main1",
):
    return {
        "turn": 3,
        "turn_player": "you",
        "phase": phase,
        "you": {
            "lp": my_lp,
            "hand": list(hand),
            "monster_zone": list(my_monsters),
            "spell_trap_zone": list(my_backrow),
            "graveyard": list(my_grave),
            "banished": [],
        },
        "opponent": {
            "lp": their_lp,
            "hand": [{"face_down": True}] * 3,
            "hand_count": 3,
            "monster_zone": list(their_monsters),
            "spell_trap_zone": list(their_backrow),
            "graveyard": list(their_grave),
            "banished": [],
        },
        "decision": decision or {},
    }


def _rank(decision, actions, observation):
    """Score a prompt and return (name, score) pairs, best first."""

    semantic = translate(decision, tuple(actions))
    scored = score_actions(semantic, extract(observation), decision)
    ordered = sorted(scored, key=lambda entry: -entry.score)
    return [
        (entry.action.name, entry.action.arguments.get("card"), entry.score)
        for entry in ordered
    ]


def _idle(*commands):
    choices = [
        {"command": command, "index": index, "card": {"name": card} if card else None}
        for index, (command, card) in enumerate(commands)
    ]
    decision = {"responder": "select_idlecmd", "choices": choices}
    actions = [
        ActionChoice("select_idlecmd", {"command": c["command"], "index": c["index"]}, "x")
        for c in choices
    ]
    return decision, actions


def test_takes_lethal_over_passing() -> None:
    decision = {
        "responder": "select_battlecmd",
        "choices": [
            {"command": "attack", "index": 0, "card": {"name": "Blade Knight"}},
            {"command": "to_end_phase", "index": None, "card": None},
        ],
    }
    actions = [
        ActionChoice("select_battlecmd", {"command": "attack", "index": 0}, "x"),
        ActionChoice("select_battlecmd", {"command": "to_end_phase", "index": None}, "x"),
    ]
    observation = _observation(
        my_monsters=[_card("Blade Knight", 1900)], their_monsters=[], their_lp=1500
    )
    ranked = _rank(decision, actions, observation)
    assert ranked[0][0] == "declare_attack"
    assert ranked[0][2] > 50, "lethal should dominate every other consideration"


def test_prefers_pot_of_greed_over_ending_the_turn() -> None:
    decision, actions = _idle(("activate", "Pot of Greed"), ("to_end_phase", None))
    ranked = _rank(decision, actions, _observation(hand=[_card("Pot of Greed")]))
    assert ranked[0][:2] == ("activate", "Pot of Greed")
    assert ranked[-1][0] == "end_turn"


def test_summons_chaos_sorcerer_when_the_cost_is_available_and_the_board_is_lost() -> None:
    decision, actions = _idle(("sp_summon", "Chaos Sorcerer"), ("to_end_phase", None))
    observation = _observation(
        their_monsters=[_card("Luster Dragon", 1900, owner="opponent")],
        my_grave=[_card("Magician of Faith", attribute="LIGHT"), _card("Sangan", attribute="DARK")],
    )
    state = extract(observation)
    assert state.chaos_ready and state.board_deficit > 0

    ranked = _rank(decision, actions, observation)
    assert ranked[0][:2] == ("special_summon", "Chaos Sorcerer")


def test_never_cancels_a_committed_cost() -> None:
    """The loop that stalled 27 benchmark duels must score worst, not first."""

    decision = {
        "responder": "select_unselect_card",
        "selectable_cards": [{"name": "Magician of Faith", "owner": "you"}],
        "selected_cards": [],
        "cancelable": True,
    }
    actions = [
        ActionChoice("select_unselect_card", {"index": 0}, "x"),
        ActionChoice("select_unselect_card", {"index": None}, "x"),
    ]
    ranked = _rank(decision, actions, _observation())
    assert ranked[0][0] == "select_card"
    assert ranked[-1][0] == "cancel_selection"


def test_does_not_fire_heavy_storm_into_an_empty_backrow() -> None:
    decision, actions = _idle(("activate", "Heavy Storm"), ("to_end_phase", None))
    empty = _rank(decision, actions, _observation(my_backrow=[_card("Scapegoat")]))
    assert empty[0][0] == "end_turn", "Storm with no targets is worse than passing"

    loaded = _rank(
        decision,
        actions,
        _observation(their_backrow=[_card("A", owner="opponent"), _card("B", owner="opponent")]),
    )
    assert loaded[0][:2] == ("activate", "Heavy Storm")


def test_nobleman_waits_for_a_face_down_target() -> None:
    decision, actions = _idle(("activate", "Nobleman of Crossout"), ("to_end_phase", None))
    no_target = _rank(decision, actions, _observation())
    assert no_target[0][0] == "end_turn"

    with_target = _rank(
        decision,
        actions,
        _observation(their_monsters=[{"owner": "opponent", "face_down": True, "position": "fd"}]),
    )
    assert with_target[0][:2] == ("activate", "Nobleman of Crossout")


def test_creature_swap_gives_away_a_goat_token_rather_than_a_real_monster() -> None:
    decision = {
        "responder": "select_card",
        "cards": [
            _card("Sheep Token", 0),
            _card("Black Luster Soldier - Envoy of the Beginning", 3000),
        ],
        "min": 1,
        "max": 1,
    }
    actions = [
        ActionChoice("select_card", {"indices": [0], "cancel": False}, "Sheep Token"),
        ActionChoice(
            "select_card",
            {"indices": [1], "cancel": False},
            "Black Luster Soldier - Envoy of the Beginning",
        ),
    ]
    ranked = _rank(decision, actions, _observation())
    assert ranked[0][0] == "choose_cards"
    best = [entry for entry in ranked if entry[2] == ranked[0][2]]
    assert len(best) == 1
    semantic = translate(decision, tuple(actions))
    scored = score_actions(semantic, extract(_observation()), decision)
    chosen = max(scored, key=lambda e: e.score)
    assert chosen.action.arguments["cards"] == ["Sheep Token"]


def test_thousand_eyes_restrict_equip_is_not_declined() -> None:
    decision = {
        "responder": "select_chain",
        "cards": [{"name": "Thousand-Eyes Restrict (GOAT)"}],
    }
    actions = [
        ActionChoice("select_chain", {"index": 0}, "Thousand-Eyes Restrict (GOAT)"),
        ActionChoice("select_chain", {"index": None}, "decline chain"),
    ]
    observation = _observation(
        my_monsters=[_card("Thousand-Eyes Restrict (GOAT)", 0)],
        their_monsters=[_card("Luster Dragon", 1900, owner="opponent")],
    )
    ranked = _rank(decision, actions, observation)
    assert ranked[0][0] == "activate_in_chain"
    assert ranked[-1][0] == "decline"


def test_avoids_attacking_into_a_larger_monster() -> None:
    decision = {
        "responder": "select_battlecmd",
        "choices": [
            {"command": "attack", "index": 0, "card": {"name": "Sangan"}},
            {"command": "to_end_phase", "index": None, "card": None},
        ],
    }
    actions = [
        ActionChoice("select_battlecmd", {"command": "attack", "index": 0}, "x"),
        ActionChoice("select_battlecmd", {"command": "to_end_phase", "index": None}, "x"),
    ]
    observation = _observation(
        my_monsters=[_card("Sangan", 1000)],
        their_monsters=[_card("Jinzo", 2400, owner="opponent")],
    )
    ranked = _rank(decision, actions, observation)
    assert ranked[0][0] == "end_turn", "walking into a bigger monster is worse than passing"


# --------------------------------------------------------------------------
# Hidden information and determinism
# --------------------------------------------------------------------------


def _request(observation, decision, actions):
    return DecisionRequest(
        player=0,
        observation={**observation, "decision": decision},
        legal_actions=tuple(actions),
        decision_type=decision["responder"],
    )


def test_changing_hidden_opponent_cards_changes_nothing() -> None:
    """Only the visible observation may influence state or choice."""

    decision, actions = _idle(
        ("activate", "Pot of Greed"), ("summon", "Sangan"), ("to_end_phase", None)
    )
    base = _observation(hand=[_card("Pot of Greed")])

    leaky = {
        **base,
        "opponent": {
            **base["opponent"],
            # A backend that wrongly disclosed the opponent's hand would put
            # real identities here.
            "hand": [
                _card("Mirror Force", owner="opponent"),
                _card("Jinzo", 2400, owner="opponent"),
            ],
        },
    }

    assert extract(base) == extract(leaky), "hidden cards must not reach the strategic state"

    agent = GoatHeuristicAgent()
    first = agent.predict(_request(base, decision, actions))
    second = agent.predict(_request(leaky, decision, actions))
    assert first == second


def test_strategic_state_only_reports_revealed_opponent_cards() -> None:
    observation = _observation(
        their_monsters=[_card("Luster Dragon", 1900, owner="opponent")],
        their_grave=[_card("Mirror Force", owner="opponent")],
    )
    state = extract(observation)
    assert state.known_opponent_cards == ("Luster Dragon", "Mirror Force")
    assert state.opponent_hand_size == 3  # counted, never identified
    assert state.premium_removal_used_by_opponent == ("Mirror Force",)


def test_decisions_are_deterministic_and_fully_traced() -> None:
    decision, actions = _idle(
        ("activate", "Pot of Greed"), ("summon", "Sangan"), ("to_end_phase", None)
    )
    observation = _observation(hand=[_card("Pot of Greed")])

    agent = GoatHeuristicAgent()
    chosen = [agent.predict(_request(observation, decision, actions)) for _ in range(5)]
    assert len(set(map(str, chosen))) == 1

    trace = agent.last_trace
    assert trace["agent"] == AGENT_VERSION
    assert trace["chosen_action"]["contributions"]
    assert len(trace["candidate_actions"]) == len(actions)
    assert trace["tie_break"]["policy"] == "lowest engine action index"
    assert trace["strategic_state"]["my_hand_size"] == 1
    # The trace keeps the exact engine payload it selected.
    assert trace["chosen_action"]["raw"]["tool"] == "select_idlecmd"


def test_agent_only_ever_returns_an_offered_action() -> None:
    decision, actions = _idle(
        ("activate", "Heavy Storm"), ("summon", "Sangan"), ("to_end_phase", None)
    )
    agent = GoatHeuristicAgent()
    chosen = agent.predict(_request(_observation(), decision, actions))
    assert chosen in actions


def test_agent_raises_rather_than_inventing_an_action() -> None:
    agent = GoatHeuristicAgent()
    with pytest.raises(ValueError, match="No legal actions"):
        agent.predict(
            DecisionRequest(
                player=0, observation={}, legal_actions=(), decision_type="select_idlecmd"
            )
        )
