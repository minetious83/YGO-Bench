"""Unit tests for the format profile, lflist parsing and legality rules.

These run without a built ocgcore or a card database.
"""

from __future__ import annotations

import pytest

from ygobench.cards import normalize_name
from ygobench.formats import FORMATS, GOAT_2005, MR5, get_format, resolve_duel_flags
from ygobench.goat.build import deck_hash
from ygobench.goat.decklists import (
    GOAT_FUSION_TOOLBOX_V1,
    REFERENCE_DECKS,
    expand,
)
from ygobench.legality import LimitList, parse_lflist, validate_deck


class _FakeIndex:
    """Minimal CardIndex stand-in: every code is its own card."""

    def limit_key(self, code: int) -> int:
        return code

    def name_of(self, code: int) -> str:
        return f"Card {code}"


def test_goat_profile_targets_project_ignis_goat_mode() -> None:
    assert GOAT_2005.duel_mode == "DUEL_MODE_GOAT"
    assert GOAT_2005.lflist == "GOAT.lflist.conf"
    assert GOAT_2005.starting_lp == 8000
    assert GOAT_2005.starting_hand == 5
    # GOAT predates the 15-card Extra Deck limit.
    assert GOAT_2005.extra_max is None
    assert MR5.extra_max == 15


def test_get_format_rejects_unknown_id() -> None:
    assert get_format(None) is MR5
    assert get_format("goat_2005") is GOAT_2005
    assert get_format(GOAT_2005) is GOAT_2005
    with pytest.raises(ValueError, match="Unknown format"):
        get_format("goat_2004")


def test_resolve_duel_flags_reads_the_engine_constant() -> None:
    class Core:
        DUEL_MODE_GOAT = 0x7F80D072C

    assert resolve_duel_flags(GOAT_2005, Core()) == 0x7F80D072C

    with pytest.raises(RuntimeError, match="DUEL_MODE_GOAT"):
        resolve_duel_flags(GOAT_2005, object())


def test_every_registered_format_is_self_consistent() -> None:
    for format_id, profile in FORMATS.items():
        assert profile.id == format_id
        assert profile.duel_mode.startswith("DUEL_MODE_")


def test_goat_lflist_is_a_whitelist(tmp_path) -> None:
    path = GOAT_2005.lflist_path
    assert path is not None and path.is_file()
    limit_list = parse_lflist(path)
    assert limit_list.whitelist is True
    # Cards outside a whitelist are illegal rather than unlimited.
    assert limit_list.limit_for(1) is None
    # Spot-check April 2005 staples.
    assert limit_list.limits[74131780] == 1  # Exiled Force
    assert limit_list.limits[36468556] == 1  # Ceasefire
    assert limit_list.limits[46411259] == 3  # Metamorphosis
    assert limit_list.limits[83764718] == 0  # Monster Reborn (Forbidden)


def test_parse_lflist_reads_limits_and_ignores_comments(tmp_path) -> None:
    path = tmp_path / "T.lflist.conf"
    path.write_text(
        "#[2005.4 T]\n!2005.4 T\n$whitelist\n#forbidden\n"
        "111 0 --Nope\n222 1 --One\n#comment\n\n333 3 --Three\n"
    )
    parsed = parse_lflist(path)
    assert parsed.name == "2005.4 T"
    assert parsed.whitelist is True
    assert parsed.limits == {111: 0, 222: 1, 333: 3}


def test_non_whitelist_defaults_to_three_copies(tmp_path) -> None:
    path = tmp_path / "U.lflist.conf"
    path.write_text("!U\n999 1 --Limited\n")
    parsed = parse_lflist(path)
    assert parsed.limit_for(999) == 1
    assert parsed.limit_for(12345) == 3


def test_validate_deck_flags_cards_outside_the_pool() -> None:
    limit_list = LimitList(name="test", limits={10: 3}, whitelist=True)
    result = validate_deck(
        deck_id="D",
        main=[10] * 39 + [99],
        side=[],
        extra=[],
        profile=GOAT_2005,
        index=_FakeIndex(),
        limit_list=limit_list,
    )
    assert not result.ok
    assert any("not in the test card pool" in error for error in result.errors)


def test_validate_deck_counts_side_and_fusion_toward_copy_limits() -> None:
    limit_list = LimitList(name="test", limits={10: 3, 20: 1}, whitelist=True)
    result = validate_deck(
        deck_id="D",
        main=[10] * 39 + [20],
        side=[20],
        extra=[],
        profile=GOAT_2005,
        index=_FakeIndex(),
        limit_list=limit_list,
    )
    assert not result.ok
    assert any("x2 exceeds 1 copy" in error for error in result.errors)


def test_validate_deck_checks_section_sizes() -> None:
    limit_list = LimitList(name="test", limits={10: 3}, whitelist=True)
    result = validate_deck(
        deck_id="D",
        main=[10] * 10,
        side=[10] * 16,
        extra=[],
        profile=GOAT_2005,
        index=_FakeIndex(),
        limit_list=limit_list,
    )
    assert any("Main Deck has 10 cards" in error for error in result.errors)
    assert any("Side Deck has 16 cards" in error for error in result.errors)


def test_goat_allows_an_oversized_fusion_deck_but_mr5_does_not() -> None:
    # Copy limits are set wide so this isolates the Extra/Fusion Deck size rule.
    limit_list = LimitList(name="test", limits={10: 40, 30: 22}, whitelist=True)
    kwargs = dict(
        deck_id="D",
        main=[10] * 40,
        side=[],
        extra=[30] * 22,
        index=_FakeIndex(),
        limit_list=limit_list,
    )
    assert validate_deck(profile=GOAT_2005, **kwargs).ok
    assert not validate_deck(profile=MR5, **kwargs).ok


def test_deck_hash_is_stable_and_content_sensitive() -> None:
    base = dict(
        main=[1, 2, 3],
        side=[4],
        fusion=[5],
        fusion_reference="TOOLBOX",
        profile=GOAT_2005,
        version=1,
    )
    assert deck_hash(**base) == deck_hash(**base)
    # Order must not matter, content must.
    assert deck_hash(**{**base, "main": [3, 2, 1]}) == deck_hash(**base)
    assert deck_hash(**{**base, "main": [1, 2, 4]}) != deck_hash(**base)
    assert deck_hash(**{**base, "side": []}) != deck_hash(**base)
    assert deck_hash(**{**base, "fusion_reference": None}) != deck_hash(**base)
    assert deck_hash(**{**base, "version": 2}) != deck_hash(**base)
    assert deck_hash(**{**base, "profile": MR5}) != deck_hash(**base)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Black Luster Soldier - Envoy of the Beginning (GOAT)",
            "black luster soldier envoy of the beginning",
        ),
        ("Ring of Destruction (Pre-Errata)", "ring of destruction"),
        ("Gravekeeper’s Spy", "gravekeeper's spy"),
        ("Level Limit – Area B", "level limit area b"),
        ("  Thousand-Eyes   Restrict ", "thousand eyes restrict"),
    ],
)
def test_normalize_name_folds_format_qualifiers_and_punctuation(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


class _FakePending:
    msg_name = "select_unselect_card"
    player = 0


def _action_set(decision: dict, passive_args: dict):
    """Run the action space against a synthetic prompt."""

    from ygobench.agents.action_space import legal_actions_from_pending

    class FakeState:
        def build_decision(self, pending, card_db):
            return decision

    class FakeReplay:
        def _pick_passive_opponent_response(self, pending):
            return decision["responder"], passive_args

    return legal_actions_from_pending(
        _FakePending(), card_db=None, replay_module=FakeReplay(), state_module=FakeState()
    )


def test_passive_entry_never_hides_a_concrete_choice() -> None:
    """The passive response can share a payload with a real choice.

    For select/unselect prompts the upstream passive response is literally
    "pick index 0".  De-duplicating first-wins would delete that choice from the
    action set, leaving the first selectable card unreachable and making the
    passive entry silently perform a selection under a misleading label.
    """

    decision = {
        "responder": "select_unselect_card",
        "selectable_cards": [{"name": "Magician of Faith"}, {"name": "Spirit Reaper"}],
        "selected_cards": [],
        "finishable": False,
    }
    actions = _action_set(decision, {"index": 0})

    payloads = [action.arguments for action in actions]
    assert {"index": 0} in payloads
    assert {"index": 1} in payloads

    merged = next(action for action in actions if action.arguments == {"index": 0})
    assert "passive / first legal" in merged.label
    assert "Magician of Faith" in merged.label

    # Every selectable card is reachable by name.
    labels = " | ".join(action.label for action in actions)
    assert "Magician of Faith" in labels and "Spirit Reaper" in labels


def test_select_unselect_labels_distinguish_select_from_unselect() -> None:
    decision = {
        "responder": "select_unselect_card",
        "selectable_cards": [{"name": "Spirit Reaper"}],
        "selected_cards": [{"name": "Magician of Faith"}],
        "finishable": True,
    }
    actions = _action_set(decision, {"index": None})
    labels = [action.label for action in actions]

    assert any(label == "select Spirit Reaper" for label in labels)
    assert any(label == "unselect Magician of Faith" for label in labels)
    # index 0 selects, index 1 toggles the already-selected card back off.
    by_index = {
        action.arguments.get("index"): action.label
        for action in actions
        if "index" in action.arguments
    }
    assert by_index[0].endswith("select Spirit Reaper")
    assert by_index[1] == "unselect Magician of Faith"


def test_reference_decks_have_the_declared_shape() -> None:
    assert len(REFERENCE_DECKS) == 10
    assert len({deck.id for deck in REFERENCE_DECKS}) == 10
    for deck in REFERENCE_DECKS:
        assert len(expand(deck.main)) == 40, deck.id
        assert len(expand(deck.side)) == 15, deck.id


def test_shuffle_is_seeded_and_conserves_every_card() -> None:
    from ygobench.engine.full_duel import _shuffled_main

    main = [code for code in range(40)] + [7, 7]
    shuffled = _shuffled_main(main, seed=5, player=0)

    # No card may be created, lost or duplicated by shuffling.
    assert sorted(shuffled) == sorted(main)
    # Reproducible for a given (seed, player), and different across both.
    assert shuffled == _shuffled_main(main, seed=5, player=0)
    assert shuffled != _shuffled_main(main, seed=6, player=0)
    assert shuffled != _shuffled_main(main, seed=5, player=1)
    # ocgcore does not shuffle at startup, so the order must actually change.
    assert shuffled != main


def test_fusion_toolbox_is_not_truncated_to_a_modern_extra_deck() -> None:
    size = len(expand(GOAT_FUSION_TOOLBOX_V1))
    # GOAT predates the 15-card limit; the list must never be silently trimmed.
    assert size > 15
    # GOAT_FUSION_TOOLBOX_V1 is officially 22 cards.
    assert size == 22
