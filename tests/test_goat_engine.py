"""GOAT engine regression tests.

These assert *observable engine behaviour* -- state read back out of ocgcore
after the duel has advanced -- rather than asserting that our own config object
says "GOAT".  A profile flag being set proves nothing about the core.

They require a built ocgcore and card database (`ygo-bench setup`).
"""

from __future__ import annotations

import json

import pytest

from ygobench.agents.random_agent import RandomAgent
from ygobench.engine.full_duel import run_duel
from ygobench.engine.upstream import UpstreamLayout
from ygobench.goat.build import DECK_ROOT

pytestmark = pytest.mark.skipif(
    bool(UpstreamLayout().runtime_errors()),
    reason="ocgcore/card database not built; run `ygo-bench setup`",
)

DECK_A = DECK_ROOT / "GOAT_CONTROL_V1.ydk"
DECK_B = DECK_ROOT / "CHAOS_CONTROL_V1.ydk"


def _run(tmp_path, *, duel_format, seed=7, max_decisions=6, name="r"):
    """Run a short duel and return its parsed replay records."""

    result = run_duel(
        DECK_A,
        DECK_B,
        agent1=RandomAgent(1),
        agent2=RandomAgent(2),
        seed=seed,
        max_decisions=max_decisions,
        output_dir=tmp_path / name,
        replay_name=f"{name}.jsonl",
        duel_format=duel_format,
    )
    records = [json.loads(line) for line in result.replay_path.read_text().splitlines()]
    return result, records


def _first_observation(records) -> dict:
    return next(r["state"] for r in records if r["type"] == "observation")


def _config(records) -> dict:
    return next(r for r in records if r["type"] == "config")


def test_goat_duel_reaches_ocgcore_and_advances(tmp_path) -> None:
    result, records = _run(tmp_path, duel_format="goat_2005")
    assert result.duel_format == "goat_2005"
    # Real engine messages, not a stubbed harness.
    assert any(r["type"] == "tool_result" and r["events"] for r in records)
    assert _first_observation(records)["turn"] == 1


def test_goat_duel_uses_the_project_ignis_goat_flag_mask(tmp_path) -> None:
    import sys

    sys.path.insert(0, str(UpstreamLayout().root / "src"))
    from engine import core  # type: ignore[import-not-found]

    _, records = _run(tmp_path, duel_format="goat_2005")
    assert _config(records)["duel_flags"] == core.DUEL_MODE_GOAT
    # Sanity: GOAT and MR5 are genuinely different rulesets.
    assert core.DUEL_MODE_GOAT != core.DUEL_MODE_MR5


def test_starting_life_points_are_8000(tmp_path) -> None:
    _, records = _run(tmp_path, duel_format="goat_2005")
    observation = _first_observation(records)
    assert observation["you"]["lp"] == 8000
    assert observation["opponent"]["lp"] == 8000


def test_first_player_draws_on_turn_one_under_goat(tmp_path) -> None:
    """The defining MR1/GOAT difference, read out of the engine state.

    Both formats deal a five-card opening hand.  Under GOAT the turn player then
    draws for turn one, so its first decision is made holding six cards; under
    MR5 there is no turn-one draw and it holds five.
    """

    _, goat = _run(tmp_path, duel_format="goat_2005", name="goat")
    _, mr5 = _run(tmp_path, duel_format="mr5", name="mr5")

    assert _config(goat)["starting_hand"] == 5
    assert _config(mr5)["starting_hand"] == 5

    goat_observation = _first_observation(goat)
    mr5_observation = _first_observation(mr5)
    assert goat_observation["turn"] == mr5_observation["turn"] == 1

    assert len(goat_observation["you"]["hand"]) == 6
    assert len(mr5_observation["you"]["hand"]) == 5
    # The opponent has not drawn for turn either way.
    assert goat_observation["opponent"]["hand_count"] == 5


def test_goat_accepts_a_fusion_deck_larger_than_fifteen(tmp_path) -> None:
    """GOAT predates the 15-card Extra Deck limit; the engine must load all 22."""

    _, records = _run(tmp_path, duel_format="goat_2005")
    observation = _first_observation(records)
    assert observation["you"]["extra_deck_count"] == 22


def test_identical_seeds_produce_identical_duels(tmp_path) -> None:
    _, first = _run(tmp_path, duel_format="goat_2005", seed=11, max_decisions=60, name="a")
    _, second = _run(tmp_path, duel_format="goat_2005", seed=11, max_decisions=60, name="b")

    def fingerprint(records):
        opening = _first_observation(records)
        actions = [
            (r["player"], r["tool_calls"][0]["name"], json.dumps(r["tool_calls"][0]["arguments"]))
            for r in records
            if r["type"] == "model_turn"
        ]
        return [card["code"] for card in opening["you"]["hand"]], actions

    assert fingerprint(first) == fingerprint(second)
    assert fingerprint(first)[1], "expected a non-empty action sequence"


def test_different_seeds_produce_different_openings(tmp_path) -> None:
    _, first = _run(tmp_path, duel_format="goat_2005", seed=1, name="s1")
    _, second = _run(tmp_path, duel_format="goat_2005", seed=2, name="s2")
    hand_of = lambda rs: [c["code"] for c in _first_observation(rs)["you"]["hand"]]  # noqa: E731
    assert hand_of(first) != hand_of(second)


def test_opponent_hand_identities_never_reach_the_acting_player(tmp_path) -> None:
    """Hidden-information filtering must survive the GOAT changes."""

    _, records = _run(tmp_path, duel_format="goat_2005", max_decisions=40)
    seen_opponent_hand = False
    for record in records:
        if record["type"] != "observation":
            continue
        for card in record["state"]["opponent"]["hand"]:
            seen_opponent_hand = True
            assert card.get("face_down") is True
            assert "code" not in card
            assert "name" not in card
    assert seen_opponent_hand, "expected to observe a hidden opponent hand"


def test_illegal_deck_is_rejected_before_the_duel_starts(tmp_path) -> None:
    """ocgcore does not validate decks, so the pre-duel gate is the only check."""

    with pytest.raises(ValueError, match="illegal for GOAT Format"):
        run_duel(
            DECK_ROOT / "EARTH_BEAT_V1.ydk",
            DECK_B,
            agent1=RandomAgent(1),
            agent2=RandomAgent(2),
            seed=1,
            max_decisions=2,
            output_dir=tmp_path / "illegal",
            duel_format="goat_2005",
        )
