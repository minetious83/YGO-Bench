"""Controlled-scenario harness for GOAT ruling tests.

Drives a real ocgcore duel under ``DUEL_MODE_GOAT`` with a stacked deck, so a
test can put specific cards in the opening hand and then interact with the
engine exactly as an agent would.  Nothing here bypasses ocgcore: the harness
only chooses deck contents and order, then plays legal actions through the same
call path the duel runner uses.

Card identities are resolved by name against the April 2005 pool, so scenarios
read as decklists rather than passcodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ygobench.agents.action_space import legal_actions_from_pending
from ygobench.cards import CardIndex
from ygobench.engine import full_duel
from ygobench.engine.protocol import ActionChoice
from ygobench.engine.upstream import UpstreamLayout
from ygobench.engine.visibility import sanitize_events_for_player
from ygobench.formats import get_format, resolve_duel_flags
from ygobench.goat.decklists import NAME_ALIASES
from ygobench.legality import parse_lflist

#: Vanilla filler used to pad a stacked deck to a legal size.  Normal monster,
#: no effect, so it never interferes with the interaction under test.
FILLER = "Luster Dragon"


@lru_cache(maxsize=1)
def _pool() -> tuple[CardIndex, set[int]]:
    layout = UpstreamLayout()
    index = CardIndex(layout.card_database_dir)
    profile = get_format("goat_2005")
    assert profile.lflist_path is not None
    return index, parse_lflist(profile.lflist_path).pool


def card_id(name: str) -> int:
    """Resolve a card name inside the April 2005 pool."""

    index, pool = _pool()
    try:
        return index.resolve(name, pool=pool)
    except Exception:
        return index.resolve(NAME_ALIASES[name], pool=pool)


def card_name(code: int) -> str:
    return _pool()[0].name_of(code)


def toolbox() -> list[int]:
    """GOAT_FUSION_TOOLBOX_V1 resolved to card ids (22 cards)."""

    from ygobench.goat.decklists import GOAT_FUSION_TOOLBOX_V1, expand

    return [card_id(name) for name in expand(GOAT_FUSION_TOOLBOX_V1)]


def stack(*names: str, size: int = 40) -> list[int]:
    """Build a Main Deck whose *last* entries are ``names``.

    ocgcore draws from the end of the order the host supplies, so the trailing
    cards are the ones dealt first.  The deck is padded at the front with a
    vanilla monster up to a legal size.
    """

    top = [card_id(name) for name in names]
    filler = card_id(FILLER)
    if len(top) > size:
        raise ValueError(f"{len(top)} stacked cards exceed deck size {size}")
    return [filler] * (size - len(top)) + top


@dataclass
class Step:
    events: list[dict[str, Any]]


class GoatDuel:
    """A live GOAT duel with a stacked deck, driven action by action."""

    def __init__(
        self,
        *,
        deck1: list[int],
        deck2: list[int] | None = None,
        extra1: list[int] | None = None,
        extra2: list[int] | None = None,
        seed: int = 0,
        duel_format: str = "goat_2005",
    ) -> None:
        layout, core, harness_mod, replay_mod, state_mod, tools_mod = full_duel._load_upstream()
        self._core = core
        self._tools = tools_mod
        self._state_mod = state_mod
        self._replay_mod = replay_mod
        self._card_db = core.CardDB(layout.root / "vendor" / "distribution" / "expansions")
        self._engine = core.OCGEngine(
            dylib_path=layout.engine_library,
            card_db=self._card_db,
            script_dir=layout.root / "vendor" / "distribution" / "script",
            card_script_dir=layout.root / "vendor" / "distribution" / "script" / "official",
        )
        profile = get_format(duel_format)
        self.flags = resolve_duel_flags(profile, core)
        full_duel._create_match(self._engine, core, seed=seed, flags=self.flags, profile=profile)
        decks = (
            {"main": list(deck1), "extra": list(extra1 or []), "side": []},
            {"main": list(deck2 if deck2 is not None else deck1),
             "extra": list(extra2 or []), "side": []},
        )
        for player, deck in enumerate(decks):
            full_duel._add_deck(self._engine, core, player=player, deck=deck)
        self._engine.start_duel()
        self.duel = harness_mod.Harness(self._engine)
        self._step = self.duel.advance()
        self._closed = False

    # -- inspection ---------------------------------------------------------

    @property
    def pending(self):
        return self.duel.pending

    @property
    def player(self) -> int:
        return int(self.duel.pending.player)

    @property
    def events(self) -> list[dict[str, Any]]:
        return self._step.events

    def observation(self, perspective: int | None = None) -> dict[str, Any]:
        """The agent-facing view for a player.

        Mirrors the duel runner exactly, including the event-stream redaction --
        ``build_state`` alone still leaks opponent draw codes through
        ``events_since_last_decision``.
        """

        observation = self.observation_raw(perspective)
        who = self.player if perspective is None else perspective
        observation["events_since_last_decision"] = sanitize_events_for_player(
            observation.get("events_since_last_decision"), perspective=who
        )
        return observation

    def observation_raw(self, perspective: int | None = None) -> dict[str, Any]:
        """The unredacted backend view -- what the server knows, not the agent."""

        who = self.player if perspective is None else perspective
        return self._state_mod.build_state(
            self.duel, self._card_db, perspective=who, events=self._step.events
        )

    def hand(self, player: int) -> list[str]:
        """Card names in a player's hand, from that player's own perspective."""

        own = self.observation(player)
        return [card_name(card["code"]) for card in own["you"]["hand"]]

    def field(self, player: int) -> list[str]:
        own = self.observation(player)
        return [card_name(c["code"]) for c in own["you"]["monster_zone"] if c.get("code")]

    def legal_actions(self):
        return legal_actions_from_pending(
            self.duel.pending,
            card_db=self._card_db,
            replay_module=self._replay_mod,
            state_module=self._state_mod,
        )

    def legal_labels(self) -> list[str]:
        return [action.label for action in self.legal_actions()]

    # -- interaction --------------------------------------------------------

    def play(self, action) -> Step:
        """Submit an ActionChoice through the normal harness call path."""

        method = getattr(self.duel, self._tools.TOOL_TO_HARNESS_METHOD[action.tool])
        arguments = full_duel._normalize_action(action, self._core, self._tools)
        self._step = method(**arguments)
        return Step(self._step.events)

    def choose(self, predicate) -> Step:
        """Play the first legal action matching ``predicate`` (label substring)."""

        if isinstance(predicate, str):
            needle = predicate.casefold()
            match = next(
                (a for a in self.legal_actions() if needle in (a.label or "").casefold()), None
            )
        else:
            match = next((a for a in self.legal_actions() if predicate(a)), None)
        if match is None:
            raise AssertionError(
                f"No legal action matching {predicate!r}; available: {self.legal_labels()}"
            )
        return self.play(match)

    def decision(self) -> dict[str, Any]:
        return self.observation().get("decision", {}) or {}

    def responder(self) -> str:
        return str(self.decision().get("responder", ""))

    def idle_choices(self) -> list[dict[str, Any]]:
        """The `(command, card)` options offered at an idle/battle prompt."""

        return list(self.decision().get("choices", []))

    def can(self, command: str, card: str | None = None) -> bool:
        return self._find_idle(command, card) is not None

    def _find_idle(self, command: str, card: str | None):
        for choice in self.idle_choices():
            if choice.get("command") != command:
                continue
            name = (choice.get("card") or {}).get("name", "")
            if card is None or card.casefold() in name.casefold():
                return choice
        return None

    def do(self, command: str, card: str | None = None) -> Step:
        """Play an idle/battle command such as ``summon`` or ``activate``."""

        choice = self._find_idle(command, card)
        if choice is None:
            offered = [
                (c.get("command"), (c.get("card") or {}).get("name")) for c in self.idle_choices()
            ]
            raise AssertionError(f"No {command!r} for {card!r}; offered: {offered}")
        action = next(
            a
            for a in self.legal_actions()
            if a.arguments.get("command") == choice.get("command")
            and a.arguments.get("index") == choice.get("index")
        )
        return self.play(action)

    def advance_to_idle(self, player: int | None = None, limit: int = 120) -> None:
        """Advance until ``player`` reaches an idle-command prompt.

        Other players' turns are passed by going straight to the End Phase, so
        an intervening turn cannot perturb the scenario under test.
        """

        for _ in range(limit):
            if self.duel.state.game_over or self.duel.pending is None:
                raise AssertionError("duel ended before reaching an idle prompt")
            responder = self.responder()
            if responder in {"select_idlecmd", "select_battlecmd"}:
                if responder == "select_idlecmd" and (player is None or self.player == player):
                    return
                if self.can("to_end_phase"):
                    self.do("to_end_phase")
                    continue
            self.auto()
        raise AssertionError(f"never reached an idle prompt for player {player}")

    def resolve(self, limit: int = 40, chooser=None) -> None:
        """Work through an effect's sub-prompts until the next idle command.

        ``chooser(responder, duel)`` may return an ActionChoice to override the
        default, which is "make the first concrete choice offered".  Note that
        the action space puts a passive entry first for most prompts; picking it
        would decline the effect, so concrete choices are preferred here.
        """

        for _ in range(limit):
            if self.pending is None or self.duel.state.game_over:
                return
            responder = self.responder()
            if responder == "select_idlecmd":
                return
            if chooser is not None:
                override = chooser(responder, self)
                if override is not None:
                    self.play(override)
                    continue
            if responder == "select_unselect_card":
                # Built by index rather than matched by label: the passive entry
                # shares its payload with "select index 0", so their labels are
                # merged and a substring match would be ambiguous.
                decision = self.decision()
                if decision.get("selectable_cards"):
                    self.play(ActionChoice(responder, {"index": 0}, "select 0"))
                else:
                    self.play(ActionChoice(responder, {"index": None}, "finish"))
                continue
            actions = self.legal_actions()
            if responder in {
                "select_card",
                "select_tribute",
                "select_place",
                "select_position",
                "select_option",
                "select_sum",
            } and len(actions) > 1:
                self.play(actions[1])
                continue
            self.auto()
        raise AssertionError(f"prompts did not settle within {limit} steps")

    def end_turn(self, limit: int = 120) -> None:
        """End the current player's turn and stop at their opponent's idle prompt."""

        current = self.player
        self.do("to_end_phase")
        self.advance_to_idle(player=1 - current, limit=limit)

    def auto(self, steps: int = 1) -> None:
        """Advance with the passive response (declines optional activations).

        Uses the action space's own passive entry rather than the raw engine
        fallback, because the latter does not produce a valid payload for every
        prompt (``select_place`` in particular).
        """

        for _ in range(steps):
            if self.duel.state.game_over or self.duel.pending is None:
                return
            self.play(self.legal_actions()[0])

    def close(self) -> None:
        if not self._closed:
            self._engine.destroy()
            self._closed = True

    def __enter__(self) -> GoatDuel:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
