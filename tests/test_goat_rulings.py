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


# --------------------------------------------------------------------------
# Batch 3: traps, lifecycles, the Damage Step and chains
# --------------------------------------------------------------------------


def test_ring_of_destruction_is_not_activatable_on_the_turn_it_is_set() -> None:
    with GoatDuel(
        deck1=stack("Ring of Destruction"), deck2=stack("Luster Dragon"), seed=3
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("set_spell", "Ring of Destruction")
        duel.resolve()
        assert not duel.can("activate", "Ring of Destruction")


def test_ring_of_destruction_damages_both_players_for_the_targets_attack() -> None:
    """Pre-errata Ring: destroy a face-up monster, both players take its ATK."""

    with GoatDuel(
        deck1=stack("Ring of Destruction"), deck2=stack("Luster Dragon"), seed=3
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("set_spell", "Ring of Destruction")
        duel.resolve()
        _opponent_plays(duel, "summon", "Luster Dragon")

        assert duel.can("activate", "Ring of Destruction")
        duel.do("activate", "Ring of Destruction")
        duel.resolve(chooser=prefer("Luster Dragon"))

        observation = duel.observation(0)
        # Luster Dragon has 1900 ATK, and the damage is symmetric.
        assert observation["you"]["lp"] == 6100
        assert observation["opponent"]["lp"] == 6100
        assert [c["name"] for c in observation["opponent"]["graveyard"]] == ["Luster Dragon"]


def _jinzo_via_premature_burial(duel: GoatDuel) -> None:
    duel.do("activate", "Graceful Charity")
    duel.resolve(chooser=prefer("Jinzo"))
    duel.do("activate", "Premature Burial")
    duel.resolve(chooser=prefer("Jinzo"))


def _opponent_trap_activation_options(with_jinzo: bool) -> list[str]:
    """Set a Trap for the opponent, then read back what they may activate."""

    with GoatDuel(
        deck1=stack("Jinzo", "Premature Burial", "Graceful Charity"),
        deck2=stack("Jar of Greed"),
        seed=3,
    ) as duel:
        duel.advance_to_idle(player=0)
        if with_jinzo:
            _jinzo_via_premature_burial(duel)
            assert "Jinzo" in _monsters(duel)
        duel.do("to_end_phase")
        duel.advance_to_idle(player=1)
        duel.do("set_spell", "Jar of Greed")
        duel.resolve()
        duel.do("to_end_phase")
        duel.advance_to_idle(player=0)
        duel.do("to_end_phase")
        duel.advance_to_idle(player=1)
        return [
            (choice.get("card") or {}).get("name", "")
            for choice in duel.idle_choices()
            if choice.get("command") == "activate"
        ]


def test_jinzo_removes_trap_activation_from_the_legal_action_set() -> None:
    """Suppression must be visible in what the engine offers, not only in outcomes."""

    without = _opponent_trap_activation_options(with_jinzo=False)
    with_jinzo = _opponent_trap_activation_options(with_jinzo=True)

    assert "Jar of Greed" in without
    assert with_jinzo == []


def test_call_of_the_haunted_revives_and_takes_the_monster_with_it() -> None:
    with GoatDuel(
        deck1=stack(
            "Luster Dragon", "Mystical Space Typhoon", "Call of the Haunted", "Graceful Charity"
        ),
        deck2=stack("Sangan"),
        seed=3,
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("activate", "Graceful Charity")
        duel.resolve(chooser=prefer("Luster Dragon"))
        duel.do("set_spell", "Call of the Haunted")
        duel.resolve()
        duel.do("to_end_phase")
        duel.advance_to_idle(player=1)
        duel.do("to_end_phase")
        duel.advance_to_idle(player=0)

        duel.do("activate", "Call of the Haunted")
        duel.resolve(chooser=prefer("Luster Dragon"))
        assert _monsters(duel) == ["Luster Dragon"]

        # Destroying Call takes the revived monster with it.
        duel.do("activate", "Mystical Space Typhoon")
        duel.resolve(chooser=prefer("Call of the Haunted"))
        assert _monsters(duel) == []
        graveyard = [c["name"] for c in duel.observation(0)["you"]["graveyard"]]
        assert "Call of the Haunted" in graveyard
        assert graveyard.count("Luster Dragon") == 2  # the discarded copy and the revived one


def _spirit_reaper_targeted_by(spell: str) -> dict:
    with GoatDuel(deck1=stack(spell), deck2=stack("Spirit Reaper"), seed=3) as duel:
        duel.advance_to_idle(player=0)
        _opponent_plays(duel, "summon", "Spirit Reaper")
        duel.do("activate", spell)
        duel.resolve(chooser=prefer("Spirit Reaper"))
        return duel.observation(0)


def test_targeting_spirit_reaper_destroys_it() -> None:
    observation = _spirit_reaper_targeted_by("Snatch Steal")
    assert [c["name"] for c in observation["opponent"]["graveyard"]] == ["Spirit Reaper (GOAT)"]
    # Destroyed on being targeted, so the equip never takes control of it.
    assert not [c for c in observation["you"]["monster_zone"] if c and c.get("name")]


def test_book_of_moon_flips_spirit_reaper_down_instead_of_destroying_it() -> None:
    """Engine-observed nuance, recorded rather than assumed.

    Spirit Reaper's self-destroy is a continuous effect ranged to the Monster
    Zone, so once Book of Moon has flipped it face-down the effect no longer
    applies when the chain solves and the Reaper survives being targeted.
    """

    observation = _spirit_reaper_targeted_by("Book of Moon")
    assert observation["opponent"]["graveyard"] == []
    face_down = [c for c in observation["opponent"]["monster_zone"] if c and c.get("face_down")]
    assert [c["position"] for c in face_down] == ["face_down_defense"]


def test_spirit_reaper_survives_battle() -> None:
    with GoatDuel(deck1=stack("Luster Dragon"), deck2=stack("Spirit Reaper"), seed=3) as duel:
        duel.advance_to_idle(player=0)
        _opponent_plays(duel, "summon", "Spirit Reaper")
        duel.do("summon", "Luster Dragon")
        duel.resolve()
        duel.do("to_battle_phase")

        for _ in range(20):
            if duel.responder() == "select_battlecmd":
                if not duel.can("attack"):
                    break
                duel.do("attack")
                continue
            if duel.responder() == "select_idlecmd":
                break
            duel.auto()

        observation = duel.observation(0)
        # 1900 - 300 of battle damage got through, but the Reaper is still there.
        assert observation["opponent"]["lp"] == 6400
        assert observation["opponent"]["graveyard"] == []


def test_damage_step_suppresses_activations_that_the_battle_step_allows() -> None:
    """Which windows expose Book of Moon is read from the engine, not listed here.

    The Damage Step is bracketed by the engine's own MSG_DAMAGE_STEP_START /
    MSG_DAMAGE_STEP_END messages, so no hand-written table of Damage Step legal
    cards is involved.
    """

    with GoatDuel(
        deck1=stack("Book of Moon", "Luster Dragon"), deck2=stack("Gemini Elf"), seed=3
    ) as duel:
        duel.advance_to_idle(player=0)
        _opponent_plays(duel, "summon", "Gemini Elf")
        duel.do("summon", "Luster Dragon")
        duel.resolve()
        assert duel.can("activate", "Book of Moon")

        duel.do("to_battle_phase")
        in_damage_step = False
        before: list[bool] = []
        during: list[bool] = []
        for _ in range(24):
            responder = duel.responder()
            if responder == "select_idlecmd":
                break
            if responder == "select_chain" and duel.player == 0:
                offered = "Book of Moon" in _activatable(duel)
                (during if in_damage_step else before).append(offered)
            if responder == "select_battlecmd":
                if not duel.can("attack"):
                    break
                duel.do("attack")
            else:
                duel.auto()
            messages = {event.get("msg_name") for event in duel.events}
            if "MSG_DAMAGE_STEP_START" in messages:
                in_damage_step = True
            if "MSG_DAMAGE_STEP_END" in messages:
                in_damage_step = False

        assert any(before), "expected Book of Moon before the Damage Step"
        assert during, "expected at least one Damage Step window"
        assert not any(during), "Book of Moon must not be activatable in the Damage Step"


def test_chain_resolves_last_in_first_out_and_a_stale_target_does_nothing() -> None:
    """Two links, reverse-order resolution, and link 1 losing its target."""

    with GoatDuel(
        deck1=stack("Ring of Destruction", "Book of Moon"),
        deck2=stack("Luster Dragon"),
        seed=3,
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("set_spell", "Ring of Destruction")
        duel.resolve()
        _opponent_plays(duel, "summon", "Luster Dragon")

        duel.do("activate", "Book of Moon")  # chain link 1
        chain_names: list[list[str]] = []
        responders: list[int] = []
        for _ in range(20):
            responder = duel.responder()
            if responder == "select_idlecmd":
                break
            chain = duel.observation().get("chain") or []
            if chain:
                chain_names.append([c.get("name", "") for c in chain])
            if responder == "select_chain":
                responders.append(duel.player)
            labels = duel.legal_labels()
            if responder == "select_card" and any("Luster" in (x or "") for x in labels):
                duel.choose("Luster Dragon")
                continue
            if responder == "select_chain" and any(
                "Ring of Destruction" in (x or "") for x in labels
            ):
                duel.choose("Ring of Destruction")  # chain link 2
                continue
            duel.auto()

        assert max(len(names) for names in chain_names) == 2
        assert ["Book of Moon", "Ring of Destruction (Pre-Errata)"] in chain_names
        # Both players get a window while the chain is open.
        assert set(responders) == {0, 1}

        observation = duel.observation(0)
        # Link 2 (Ring) resolved first: the monster is destroyed and both players
        # took its ATK...
        assert (observation["you"]["lp"], observation["opponent"]["lp"]) == (6100, 6100)
        assert [c["name"] for c in observation["opponent"]["graveyard"]] == ["Luster Dragon"]
        # ...so link 1 (Book of Moon) resolved with nothing to flip.
        assert not [c for c in observation["opponent"]["monster_zone"] if c and c.get("position")]


# --------------------------------------------------------------------------
# Milestone 0.25: the two deferred follow-ups
# --------------------------------------------------------------------------


def _call_of_the_haunted_then(flip_face_down: bool, removal: str) -> dict:
    """Revive with Call, optionally flip the monster down, then apply ``removal``."""

    with GoatDuel(
        deck1=stack("Luster Dragon", removal, "Book of Moon", "Call of the Haunted",
                    "Graceful Charity"),
        deck2=stack("Sangan"),
        seed=3,
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("activate", "Graceful Charity")
        duel.resolve(chooser=prefer("Luster Dragon"))
        duel.do("set_spell", "Call of the Haunted")
        duel.resolve()
        duel.do("to_end_phase")
        duel.advance_to_idle(player=1)
        duel.do("to_end_phase")
        duel.advance_to_idle(player=0)

        duel.do("activate", "Call of the Haunted")
        duel.resolve(chooser=prefer("Luster Dragon"))
        assert _monsters(duel) == ["Luster Dragon"]

        if flip_face_down:
            duel.do("activate", "Book of Moon")
            duel.resolve(chooser=prefer("Luster Dragon"))

        duel.do("activate", removal)
        duel.resolve(chooser=prefer("Call of the Haunted", "Luster Dragon"))
        return duel.observation(0)


def test_book_of_moon_severs_the_call_of_the_haunted_link() -> None:
    """Flipping the revived monster face-down breaks the link both ways.

    Engine-observed, not asserted from card text: while the monster is face-up,
    destroying Call destroys it; once Book of Moon has turned it face-down the
    two stop being tied together.
    """

    face_up = _call_of_the_haunted_then(False, "Mystical Space Typhoon")
    assert not [c for c in face_up["you"]["monster_zone"] if c and c.get("position")]

    face_down = _call_of_the_haunted_then(True, "Mystical Space Typhoon")
    survivor = [c for c in face_down["you"]["monster_zone"] if c and c.get("position")]
    assert [c["position"] for c in survivor] == ["face_down_defense"]


def test_call_of_the_haunted_survives_the_face_down_monster_leaving() -> None:
    """The other direction of the severed link: the monster goes, Call stays."""

    observation = _call_of_the_haunted_then(True, "Nobleman of Crossout")
    assert not [c for c in observation["you"]["monster_zone"] if c and c.get("position")]
    assert [c["name"] for c in observation["you"]["banished"]] == ["Luster Dragon"]
    still_there = [c["name"] for c in observation["you"]["spell_trap_zone"] if c and c.get("name")]
    assert "Call of the Haunted" in still_there


def test_jinzo_arriving_mid_chain_negates_a_trap_already_on_the_chain() -> None:
    """Built from ordinary GOAT cards: Call of the Haunted is a Trap, so it chains.

    Chain link 1 is Mystical Space Typhoon, link 2 the opponent's Jar of Greed,
    link 3 Call of the Haunted reviving Jinzo.  Link 3 resolves first, putting
    Jinzo on the field before link 2 resolves -- and Jar of Greed then draws
    nothing.
    """

    with GoatDuel(
        deck1=stack("Jinzo", "Mystical Space Typhoon", "Call of the Haunted", "Graceful Charity"),
        deck2=stack("Jar of Greed"),
        seed=3,
    ) as duel:
        duel.advance_to_idle(player=0)
        duel.do("activate", "Graceful Charity")
        duel.resolve(chooser=prefer("Jinzo"))
        duel.do("set_spell", "Call of the Haunted")
        duel.resolve()
        duel.do("to_end_phase")
        duel.advance_to_idle(player=1)
        duel.do("set_spell", "Jar of Greed")
        duel.resolve()
        duel.do("to_end_phase")
        duel.advance_to_idle(player=0)

        deck_before = duel.observation(1)["you"]["deck_count"]
        duel.do("activate", "Mystical Space Typhoon")  # chain link 1

        chains: list[list[str]] = []
        for _ in range(24):
            responder = duel.responder()
            if responder == "select_idlecmd":
                break
            chain = [c.get("name", "") for c in (duel.observation().get("chain") or [])]
            if chain:
                chains.append(chain)
            labels = duel.legal_labels()
            if responder == "select_chain" and duel.player == 1 and any(
                "Jar of Greed" in (x or "") for x in labels
            ):
                duel.choose("Jar of Greed")  # chain link 2
                continue
            if responder == "select_chain" and duel.player == 0 and any(
                "Call of the Haunted" in (x or "") for x in labels
            ):
                duel.choose("Call of the Haunted")  # chain link 3
                continue
            if responder == "select_card":
                match = next(
                    (a for a in duel.legal_actions() if "Jinzo" in (a.label or "")), None
                )
                if match is not None:
                    duel.play(match)
                    continue
            duel.auto()

        assert max(len(c) for c in chains) == 3
        assert _monsters(duel) == ["Jinzo"]
        # Jar of Greed was already on the chain, but resolves under Jinzo and
        # draws nothing.
        assert duel.observation(1)["you"]["deck_count"] == deck_before
