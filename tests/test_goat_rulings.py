"""GOAT ruling regression suite (Milestone 0.2, batch 1).

Every assertion here is about behaviour observed from ocgcore after the duel has
advanced -- what the engine offers, banishes or summons -- never about our own
configuration values.  Where a ruling distinguishes GOAT from modern rules, the
test runs the *same* scenario under both rulesets and asserts the difference, so
a passing test cannot be explained by the scenario alone.

If Project Ignis ever disagrees with an expected GOAT ruling, the correct
response is to record the failing scenario and report the engine's actual
behaviour -- not to encode the expected ruling in Python.
"""

from __future__ import annotations

import pytest
from goat_harness import GoatDuel, card_id, stack, toolbox

from ygobench.engine.upstream import UpstreamLayout

pytestmark = pytest.mark.skipif(
    bool(UpstreamLayout().runtime_errors()),
    reason="ocgcore/card database not built; run `ygo-bench setup`",
)


def _activatable(duel: GoatDuel) -> list[str]:
    return [card.get("name", "") for card in duel.decision().get("cards", [])]


def _monsters(duel: GoatDuel, player: int = 0) -> list[str]:
    zone = duel.observation(player)["you"]["monster_zone"]
    return [card["name"] for card in zone if card and card.get("name")]


# --------------------------------------------------------------------------
# 1. Legacy ignition-effect priority after a summon
# --------------------------------------------------------------------------


def _post_summon_windows(duel_format: str) -> list[tuple[int, list[str]]]:
    """Normal Summon Breaker, then record each chain window that follows."""

    with GoatDuel(
        deck1=stack("Book of Moon", "Breaker the Magical Warrior"),
        deck2=stack("Mirror Force", "Sangan"),
        seed=3,
        duel_format=duel_format,
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("set_spell", "Book of Moon")  # a target for Breaker's ignition
        duel.advance_to_idle(player=0)
        duel.do("summon", "Breaker the Magical Warrior")

        windows: list[tuple[int, list[str]]] = []
        for _ in range(8):
            if duel.pending is None:
                break
            responder = duel.responder()
            if responder == "select_place":
                duel.auto()
                continue
            if responder == "select_idlecmd":
                break
            windows.append((duel.player, _activatable(duel)))
            duel.auto()
        return windows


def test_goat_grants_ignition_priority_to_the_summoning_player() -> None:
    """The summoning player may use Breaker's ignition in a post-summon window.

    Under GOAT (legacy priority) Breaker is offered to the turn player in a chain
    window straight after the Normal Summon.  Under Master Rule 5 it is not: the
    ignition effect only becomes available later, in an open game state.
    """

    goat = _post_summon_windows("goat_2005")
    mr5 = _post_summon_windows("mr5")

    goat_offers = [(player, cards) for player, cards in goat if "Breaker" in " ".join(cards)]
    mr5_offers = [(player, cards) for player, cards in mr5 if "Breaker" in " ".join(cards)]

    assert goat_offers, f"GOAT offered no post-summon ignition window: {goat}"
    assert all(player == 0 for player, _ in goat_offers), "priority belongs to the summoning player"
    assert not mr5_offers, f"MR5 unexpectedly offered a post-summon ignition window: {mr5}"


# --------------------------------------------------------------------------
# 2. Scapegoat
# --------------------------------------------------------------------------


def test_scapegoat_creates_four_level_one_sheep_tokens() -> None:
    with GoatDuel(deck1=stack("Scapegoat"), deck2=stack("Sangan"), seed=3) as duel:
        duel.advance_to_idle(player=0)
        duel.do("activate", "Scapegoat")
        duel.advance_to_idle(player=0)

        zone = duel.observation(0)["you"]["monster_zone"]
        tokens = [card for card in zone if card and card.get("name") == "Sheep Token"]
        assert len(tokens) == 4
        assert {token["level"] for token in tokens} == {1}
        assert all(token["position"] == "face_up_defense" for token in tokens)


def test_goat_scapegoat_locks_out_every_summon_for_the_turn() -> None:
    """Pre-errata Scapegoat stops *all* summoning, not just Normal Summons.

    This is why the Metamorphosis-on-a-Goat line was always a two-turn play in
    2005: the Fusion Summon has to wait until the following turn.
    """

    with GoatDuel(
        deck1=stack("Metamorphosis", "Scapegoat"),
        extra1=toolbox(),
        deck2=stack("Sangan"),
        seed=3,
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("activate", "Scapegoat")
        duel.advance_to_idle(player=0)

        assert not duel.can("summon"), "Normal Summon should be locked out"
        # Metamorphosis is a Special Summon, so it is locked out this turn too.
        assert not duel.can("activate", "Metamorphosis")
        # Setting a monster is not a Summon, and the 2005 text does not forbid
        # it -- the script registers CANNOT_SUMMON / CANNOT_FLIP_SUMMON /
        # CANNOT_SPECIAL_SUMMON only.  Verified against the engine.
        assert duel.can("set_monster"), "Setting a monster is still permitted"

        duel.end_turn()
        duel.advance_to_idle(player=0)
        assert duel.can("activate", "Metamorphosis"), "the lock must expire at end of turn"


# --------------------------------------------------------------------------
# 3-5. Metamorphosis
# --------------------------------------------------------------------------


def test_metamorphosis_on_a_sheep_token_summons_thousand_eyes_restrict() -> None:
    """The defining GOAT line: Level 1 token -> Level 1 Fusion."""

    with GoatDuel(
        deck1=stack("Metamorphosis", "Scapegoat"),
        extra1=toolbox(),
        deck2=stack("Sangan"),
        seed=3,
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("activate", "Scapegoat")
        duel.advance_to_idle(player=0)
        duel.end_turn()
        duel.advance_to_idle(player=0)

        offered: list[list[str]] = []

        def record(responder, active):
            if responder == "select_card":
                names = _activatable(active)
                if names:
                    offered.append(names)
            return None

        duel.do("activate", "Metamorphosis")
        duel.resolve(chooser=record)

        # Tribute choice is among the tokens; the Fusion choice is Level 1 only.
        assert offered[0] == ["Sheep Token"] * 4
        assert set(offered[1]) == {"Thousand-Eyes Restrict (GOAT)"}

        field = _monsters(duel)
        assert "Thousand-Eyes Restrict (GOAT)" in field
        assert field.count("Sheep Token") == 3


def _chaos_sorcerer_on_field(duel_format: str = "goat_2005") -> GoatDuel:
    """Graceful Charity discards a LIGHT and a DARK, then Chaos Sorcerer banishes them."""

    duel = GoatDuel(
        deck1=stack(
            "Spirit Reaper", "Magician of Faith", "Metamorphosis", "Chaos Sorcerer",
            "Graceful Charity",
        ),
        extra1=toolbox(),
        deck2=stack("Sangan"),
        seed=3,
        duel_format=duel_format,
    )
    duel.advance_to_idle(player=0)
    duel.do("activate", "Graceful Charity")

    def discard_light_and_dark(responder, active):
        if responder == "select_card":
            return next(
                (
                    action
                    for action in active.legal_actions()
                    if "Magician of Faith" in (action.label or "")
                    and "Spirit Reaper" in (action.label or "")
                ),
                None,
            )
        return None

    duel.resolve(chooser=discard_light_and_dark)
    return duel


def test_chaos_sorcerer_special_summons_by_banishing_one_light_and_one_dark() -> None:
    with _chaos_sorcerer_on_field() as duel:
        assert duel.can("sp_summon", "Chaos Sorcerer")
        duel.do("sp_summon", "Chaos Sorcerer")
        duel.resolve()

        assert _monsters(duel) == ["Chaos Sorcerer"]
        banished = {card["name"] for card in duel.observation(0)["you"]["banished"]}
        assert banished == {"Magician of Faith", "Spirit Reaper (GOAT)"}


def test_metamorphosis_on_chaos_sorcerer_offers_only_level_six_fusions() -> None:
    """Level matching, checked against the engine's own offered set."""

    with _chaos_sorcerer_on_field() as duel:
        duel.do("sp_summon", "Chaos Sorcerer")
        duel.resolve()

        offered: list[list[str]] = []

        def record(responder, active):
            if responder == "select_card":
                names = _activatable(active)
                if names:
                    offered.append(names)
            return None

        duel.do("activate", "Metamorphosis")
        duel.resolve(chooser=record)

        assert offered[0] == ["Chaos Sorcerer"]
        # Every Level 6 Fusion in the 22-card toolbox, and nothing else.
        assert set(offered[1]) == {
            "Ryu Senshi",
            "Dark Blade the Dragon Knight",
            "Ojama King",
            "Dark Flare Knight",
            "Roaring Ocean Snake",
        }
        assert "Thousand-Eyes Restrict (GOAT)" not in offered[1]


def test_metamorphosis_from_chaos_sorcerer_summons_ryu_senshi() -> None:
    with _chaos_sorcerer_on_field() as duel:
        duel.do("sp_summon", "Chaos Sorcerer")
        duel.resolve()

        def take_ryu_senshi(responder, active):
            if responder == "select_card":
                return next(
                    (a for a in active.legal_actions() if (a.label or "").startswith("Ryu Senshi")),
                    None,
                )
            return None

        duel.do("activate", "Metamorphosis")
        duel.resolve(chooser=take_ryu_senshi)

        assert _monsters(duel) == ["Ryu Senshi"]


# --------------------------------------------------------------------------
# Batch 2: board-control interactions
# --------------------------------------------------------------------------


def prefer(*names: str):
    """Chooser that picks the first offered action matching any of ``names``."""

    def chooser(responder, duel):
        if responder in {"select_card", "select_chain", "select_tribute"}:
            for name in names:
                needle = name.casefold()
                match = next(
                    (a for a in duel.legal_actions() if needle in (a.label or "").casefold()),
                    None,
                )
                if match is not None:
                    return match
        return None

    return chooser


def _opponent_plays(duel: GoatDuel, command: str, card: str) -> None:
    """Hand the turn to the opponent, have them commit a card, and hand it back."""

    duel.do("to_end_phase")
    duel.advance_to_idle(player=1)
    duel.do(command, card)
    duel.resolve()
    duel.do("to_end_phase")
    duel.advance_to_idle(player=0)


def test_thousand_eyes_restrict_equips_the_opponents_monster() -> None:
    """The signature GOAT steal: TER takes the monster and copies its stats."""

    with GoatDuel(
        deck1=stack("Metamorphosis", "Scapegoat"),
        extra1=toolbox(),
        deck2=stack("Luster Dragon"),
        seed=3,
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("activate", "Scapegoat")
        duel.advance_to_idle(player=0)
        _opponent_plays(duel, "summon", "Luster Dragon")

        opponent_field = [
            c["name"]
            for c in duel.observation(0)["opponent"]["monster_zone"]
            if c and c.get("name")
        ]
        assert opponent_field == ["Luster Dragon"]

        duel.do("activate", "Metamorphosis")
        duel.resolve(chooser=prefer("Thousand-Eyes Restrict", "Luster Dragon", "Sheep Token"))

        mine = {
            card["name"]: card
            for card in duel.observation(0)["you"]["monster_zone"]
            if card and card.get("name")
        }
        assert "Thousand-Eyes Restrict (GOAT)" in mine
        # The equipped monster leaves the opponent's field...
        assert not [
            c for c in duel.observation(0)["opponent"]["monster_zone"] if c and c.get("name")
        ]
        # ...and TER takes on its ATK/DEF.
        equipped = mine["Thousand-Eyes Restrict (GOAT)"]
        assert (equipped["attack"], equipped["defense"]) == (1900, 1600)


def test_book_of_moon_flips_a_face_up_monster_face_down() -> None:
    with GoatDuel(deck1=stack("Book of Moon"), deck2=stack("Luster Dragon"), seed=3) as duel:
        duel.advance_to_idle(player=0)
        _opponent_plays(duel, "summon", "Luster Dragon")

        duel.do("activate", "Book of Moon")
        duel.resolve(chooser=prefer("Luster Dragon"))

        flipped = [
            c
            for c in duel.observation(0)["opponent"]["monster_zone"]
            if c and c.get("face_down")
        ]
        assert len(flipped) == 1
        assert flipped[0]["position"] == "face_down_defense"
        # Face-down means face-down: its identity is not disclosed.
        assert flipped[0].get("name") is None
        assert "code" not in flipped[0]


def test_creature_swap_exchanges_control_of_a_goat_token() -> None:
    with GoatDuel(
        deck1=stack("Creature Swap", "Scapegoat"), deck2=stack("Luster Dragon"), seed=3
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("activate", "Scapegoat")
        duel.advance_to_idle(player=0)
        _opponent_plays(duel, "summon", "Luster Dragon")

        duel.do("activate", "Creature Swap")
        duel.resolve(chooser=prefer("Sheep Token", "Luster Dragon"))

        mine = [
            c["name"] for c in duel.observation(0)["you"]["monster_zone"] if c and c.get("name")
        ]
        theirs = [
            c["name"]
            for c in duel.observation(0)["opponent"]["monster_zone"]
            if c and c.get("name")
        ]
        assert "Luster Dragon" in mine
        assert theirs == ["Sheep Token"]
        assert mine.count("Sheep Token") == 3


def test_nobleman_of_crossout_banishes_a_flip_monster_and_its_deck_copies() -> None:
    """The Flip clause: the target and every same-named copy are banished, not sent to the GY."""

    magician = card_id("Magician of Faith")
    filler = card_id("Luster Dragon")
    # One copy reaches the hand to be Set; two stay in the Deck.
    opponent_deck = [magician] * 2 + [filler] * 37 + [magician]

    with GoatDuel(
        deck1=stack("Nobleman of Crossout"), deck2=opponent_deck, seed=3
    ) as duel:
        duel.advance_to_idle(player=0)
        _opponent_plays(duel, "set_monster", "Magician of Faith")

        before = duel.observation(0)["opponent"]["deck_count"]

        duel.do("activate", "Nobleman of Crossout")
        duel.resolve()

        after = duel.observation(0)["opponent"]
        assert not [c for c in after["monster_zone"] if c and c.get("name")]
        # Banished, never sent to the graveyard -- Magician of Faith must not
        # get the chance to be revived or flipped later.
        assert after["graveyard"] == []
        assert [c["name"] for c in after["banished"]] == ["Magician of Faith"] * 3
        assert before - after["deck_count"] == 2


def test_tsukuyomi_flips_a_monster_down_then_returns_to_the_hand() -> None:
    """FLIP effect plus the Spirit return: Tsukuyomi never stays on the field."""

    with GoatDuel(deck1=stack("Tsukuyomi"), deck2=stack("Luster Dragon"), seed=3) as duel:
        duel.advance_to_idle(player=0)
        duel.do("set_monster", "Tsukuyomi")
        duel.resolve()
        _opponent_plays(duel, "summon", "Luster Dragon")

        # A Flip Summon is offered as a reposition of the Set monster.
        duel.do("repos", "Tsukuyomi")
        duel.resolve(chooser=prefer("Luster Dragon"))

        flipped = [
            c for c in duel.observation(0)["opponent"]["monster_zone"] if c and c.get("position")
        ]
        assert [c["position"] for c in flipped] == ["face_down_defense"]
        assert _monsters(duel) == ["Tsukuyomi"]

        # Spirit monsters go back to the hand at the End Phase.
        duel.do("to_end_phase")
        duel.advance_to_idle(player=1)
        assert _monsters(duel) == []
        assert "Tsukuyomi" in duel.hand(0)


def test_sinister_serpent_returns_from_the_graveyard_in_the_standby_phase() -> None:
    with GoatDuel(
        deck1=stack("Sinister Serpent", "Graceful Charity"), deck2=stack("Sangan"), seed=3
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("activate", "Graceful Charity")
        duel.resolve(chooser=prefer("Sinister Serpent"))

        graveyard = [c["name"] for c in duel.observation(0)["you"]["graveyard"]]
        assert "Sinister Serpent (Pre-Errata)" in graveyard

        duel.do("to_end_phase")
        duel.advance_to_idle(player=1)
        duel.do("to_end_phase")

        offered_in: str | None = None
        for _ in range(14):
            if duel.responder() == "select_idlecmd" and duel.player == 0:
                break
            if duel.responder() == "select_effectyn":
                offered_in = duel.observation().get("phase")
                duel.choose("accept")
                continue
            duel.auto()

        # The recovery is a Standby Phase effect, not an any-time one.
        assert offered_in == "standby"
        assert "Sinister Serpent (Pre-Errata)" in duel.hand(0)
        assert "Sinister Serpent (Pre-Errata)" not in [
            c["name"] for c in duel.observation(0)["you"]["graveyard"]
        ]
