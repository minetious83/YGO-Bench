"""Tests for the semantic action layer.

The layer is a translation of engine legal actions, so every test asserts the
mapping back to a real payload rather than any independent notion of legality.
"""

from __future__ import annotations

from ygobench.agents.semantics import has_true_decline, translate
from ygobench.engine.protocol import ActionChoice


def _raw(tool: str, **arguments) -> ActionChoice:
    return ActionChoice(tool=tool, arguments=arguments, label="raw")


def test_flip_summon_is_named_by_the_state_of_the_monster() -> None:
    """`repos` is a Flip Summon for a face-down monster, a position change otherwise."""

    decision = {
        "responder": "select_idlecmd",
        "choices": [
            {"command": "repos", "index": 0, "card": {"name": "Tsukuyomi", "face_down": True}},
            {"command": "repos", "index": 1, "card": {"name": "Luster Dragon", "face_down": False}},
        ],
    }
    actions = (
        _raw("select_idlecmd", command="repos", index=0),
        _raw("select_idlecmd", command="repos", index=1),
    )
    result = translate(decision, actions)

    flip, reposition = result.actions
    assert (flip.name, flip.arguments) == ("flip_summon", {"card": "Tsukuyomi"})
    assert flip.description == "Flip Summon Tsukuyomi"
    assert reposition.name == "change_position"
    # Both still map back to the exact engine payload.
    assert flip.raw.arguments == {"command": "repos", "index": 0}
    assert reposition.raw.arguments == {"command": "repos", "index": 1}


def test_attack_declaration_names_the_attacker() -> None:
    decision = {
        "responder": "select_battlecmd",
        "choices": [
            {"command": "attack", "index": 0, "card": {"name": "Blade Knight"}},
            {"command": "to_main_phase_2", "index": None},
            {"command": "to_end_phase", "index": None},
        ],
    }
    actions = (
        _raw("select_battlecmd", command="attack", index=0),
        _raw("select_battlecmd", command="to_main_phase_2", index=None),
        _raw("select_battlecmd", command="to_end_phase", index=None),
    )
    result = translate(decision, actions)

    assert [a.name for a in result.actions] == [
        "declare_attack",
        "enter_main_phase_2",
        "end_turn",
    ]
    assert result.actions[0].arguments == {"card": "Blade Knight"}
    assert result.actions[0].raw.arguments == {"command": "attack", "index": 0}


def test_optional_effect_yes_no_is_explicit_about_declining() -> None:
    decision = {"responder": "select_effectyn"}
    actions = (_raw("select_effectyn", accept=False), _raw("select_effectyn", accept=True))
    result = translate(decision, actions)

    decline, accept = result.actions
    assert (decline.name, decline.is_decline) == ("decline", True)
    assert (accept.name, accept.is_decline) == ("accept_effect", False)
    assert has_true_decline(result)


def test_select_unselect_distinguishes_selecting_from_unselecting() -> None:
    decision = {
        "responder": "select_unselect_card",
        "selectable_cards": [{"name": "Spirit Reaper"}],
        "selected_cards": [{"name": "Magician of Faith"}],
        "finishable": True,
    }
    actions = (
        _raw("select_unselect_card", index=0),
        _raw("select_unselect_card", index=1),
        _raw("select_unselect_card", index=None),
    )
    result = translate(decision, actions)

    assert [(a.name, a.arguments) for a in result.actions] == [
        ("select_card", {"card": "Spirit Reaper"}),
        ("unselect_card", {"card": "Magician of Faith"}),
        ("finish_selection", {}),
    ]
    # Paying a cost is not a decline, even though finishing is available.
    assert not has_true_decline(result)
    assert all(not a.is_decline for a in result.actions)


def test_chain_prompt_marks_the_real_decline() -> None:
    decision = {"responder": "select_chain", "cards": [{"name": "Mirror Force"}]}
    actions = (_raw("select_chain", index=0), _raw("select_chain", index=None))
    result = translate(decision, actions)

    activate, decline = result.actions
    assert (activate.name, activate.arguments) == (
        "activate_in_chain",
        {"card": "Mirror Force"},
    )
    assert (decline.name, decline.is_decline) == ("decline", True)
    assert has_true_decline(result)


def test_translation_is_total_order_preserving_and_payload_exact() -> None:
    """Every action is translated, in order, wrapping its original payload."""

    decision = {
        "responder": "select_card",
        "cards": [{"name": "A"}, {"name": "B"}],
        "cancelable": True,
    }
    actions = (
        _raw("select_card", indices=[0], cancel=False),
        _raw("select_card", indices=[1], cancel=False),
        _raw("select_card", indices=[], cancel=True),
    )
    result = translate(decision, actions)

    assert len(result.actions) == len(actions)
    assert [a.index for a in result.actions] == [0, 1, 2]
    for semantic, original in zip(result.actions, actions, strict=True):
        assert semantic.raw is original
    assert result.actions[0].arguments == {"cards": ["A"]}
    assert result.actions[2].is_decline is True


def test_semantic_action_serializes_with_its_raw_payload() -> None:
    decision = {
        "responder": "select_idlecmd",
        "choices": [{"command": "summon", "index": 0, "card": {"name": "Sangan"}}],
    }
    actions = (_raw("select_idlecmd", command="summon", index=0),)
    payload = translate(decision, actions).to_dict()

    assert payload["responder"] == "select_idlecmd"
    entry = payload["actions"][0]
    assert entry["name"] == "normal_summon"
    assert entry["arguments"] == {"card": "Sangan"}
    # The raw engine payload is retained for replay/debug.
    assert entry["raw"] == {
        "tool": "select_idlecmd",
        "arguments": {"command": "summon", "index": 0},
    }


def test_unknown_prompts_degrade_without_inventing_actions() -> None:
    decision = {"responder": "announce_number", "numbers": [1, 2]}
    actions = (_raw("announce_number", index=0), _raw("announce_number", index=1))
    result = translate(decision, actions)

    assert len(result.actions) == 2
    assert all(a.raw.tool == "announce_number" for a in result.actions)
    assert not has_true_decline(result)


def test_finishing_is_distinguished_from_cancelling_a_selection() -> None:
    """`index: None` cancels an empty selection but finishes a started one.

    Naming both "finish" let a deterministic agent abort a cost it had just
    chosen to pay, return to the idle prompt, choose it again, and loop forever.
    """

    empty = {
        "responder": "select_unselect_card",
        "selectable_cards": [{"name": "Magician of Faith"}],
        "selected_cards": [],
        "cancelable": True,
    }
    started = {
        "responder": "select_unselect_card",
        "selectable_cards": [{"name": "Spirit Reaper"}],
        "selected_cards": [{"name": "Magician of Faith"}],
        "finishable": True,
    }
    actions = (_raw("select_unselect_card", index=0), _raw("select_unselect_card", index=None))

    cancel = translate(empty, actions).actions[1]
    assert (cancel.name, cancel.is_decline) == ("cancel_selection", True)

    finish = translate(started, actions).actions[1]
    assert (finish.name, finish.is_decline) == ("finish_selection", False)


def test_engine_draw_code_maps_to_no_winning_seat() -> None:
    """ocgcore reports a draw as MSG_WIN winner=2.

    Indexing the agent tuple with that value crashed the duel runner outright.
    GOAT reaches draws naturally: Ring of Destruction deals its damage to both
    players, so a simultaneous 0 LP is a real result, not a failure.
    """

    from ygobench.engine.full_duel import _normalize_winner

    assert _normalize_winner(0) == 0
    assert _normalize_winner(1) == 1
    assert _normalize_winner(2) is None  # draw
    assert _normalize_winner(None) is None
    assert _normalize_winner(-1) is None
