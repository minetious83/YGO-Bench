"""Derived GOAT strategic state, built only from what a player can see.

This is a *reading* of the observation the acting player already receives -- it
adds no information and resolves no rules.  It is deliberately not a second
rules engine: it counts, sums and classifies, and everything it cannot see stays
unknown.

The same derived state is handed to the heuristic agent today and will be handed
to an LLM agent later, so the two differ in how they rank choices rather than in
what they know.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

#: Cards whose presence materially changes GOAT decisions.
PREMIUM_REMOVAL = {
    "Mirror Force",
    "Torrential Tribute",
    "Ring of Destruction",
    "Heavy Storm",
    "Dark Hole",
    "Raigeki",
}
DRAW_SPELLS = {"Pot of Greed", "Graceful Charity"}
CHAOS_MONSTERS = {
    "Chaos Sorcerer",
    "Black Luster Soldier - Envoy of the Beginning",
    "Dark Magician of Chaos",
}
TOKEN_NAME = "Sheep Token"


def base_name(name: str | None) -> str:
    """Strip the `(GOAT)` / `(Pre-Errata)` qualifier from a printed name."""

    if not name:
        return ""
    for suffix in (" (GOAT)", " (Pre-Errata)"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _cards(zone: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [card for card in (zone or []) if card]


def _named(zone: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Only the cards whose identity is actually visible."""

    return [card for card in _cards(zone) if card.get("name")]


def _attack(card: dict[str, Any]) -> int:
    try:
        return int(card.get("attack") or 0)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class GoatStrategicState:
    my_lp: int
    opponent_lp: int
    my_hand_size: int
    opponent_hand_size: int
    my_monster_count: int
    opponent_monster_count: int
    my_backrow_count: int
    opponent_backrow_count: int
    my_face_down_monsters: int
    opponent_face_down_monsters: int
    my_goat_tokens: int
    opponent_goat_tokens: int
    lights_in_my_grave: int
    darks_in_my_grave: int
    my_best_attack: int
    opponent_best_attack: int
    known_opponent_cards: tuple[str, ...]
    premium_removal_used_by_opponent: tuple[str, ...]
    estimated_battle_damage: int
    is_my_turn: bool
    phase: str
    turn: int

    @property
    def chaos_ready(self) -> bool:
        """A Chaos monster's banish cost is naturally payable."""

        return self.lights_in_my_grave >= 1 and self.darks_in_my_grave >= 1

    @property
    def opponent_field_open(self) -> bool:
        return self.opponent_monster_count == 0

    @property
    def board_deficit(self) -> int:
        return self.opponent_monster_count - self.my_monster_count

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["chaos_ready"] = self.chaos_ready
        data["opponent_field_open"] = self.opponent_field_open
        data["board_deficit"] = self.board_deficit
        return data


def extract(observation: dict[str, Any]) -> GoatStrategicState:
    """Derive the strategic state from one player-visible observation."""

    me = observation.get("you", {}) or {}
    them = observation.get("opponent", {}) or {}

    my_monsters = _named(me.get("monster_zone"))
    their_monsters = _named(them.get("monster_zone"))
    my_grave = _named(me.get("graveyard"))

    attributes = [str(card.get("attribute") or "") for card in my_grave]
    my_attacks = [_attack(card) for card in my_monsters]
    their_attacks = [_attack(card) for card in their_monsters]
    best_mine = max(my_attacks, default=0)

    # Only cards whose identity the opponent has actually revealed: face-up
    # field, graveyard and banished zone.  Never their hand.
    known: list[str] = []
    for zone in ("monster_zone", "spell_trap_zone", "graveyard", "banished"):
        known += [base_name(card.get("name")) for card in _named(them.get(zone))]

    # If their field is empty, every attacker connects; otherwise assume the
    # best attacker trades with their best monster.
    if not their_monsters:
        damage = sum(my_attacks)
    else:
        damage = max(0, best_mine - max(their_attacks, default=0))

    return GoatStrategicState(
        my_lp=int(me.get("lp") or 0),
        opponent_lp=int(them.get("lp") or 0),
        my_hand_size=len(_cards(me.get("hand"))),
        opponent_hand_size=int(them.get("hand_count") or 0),
        my_monster_count=len(_cards(me.get("monster_zone"))),
        opponent_monster_count=len(_cards(them.get("monster_zone"))),
        my_backrow_count=len(_cards(me.get("spell_trap_zone"))),
        opponent_backrow_count=len(_cards(them.get("spell_trap_zone"))),
        my_face_down_monsters=sum(
            1 for card in _cards(me.get("monster_zone")) if card.get("face_down")
        ),
        opponent_face_down_monsters=sum(
            1 for card in _cards(them.get("monster_zone")) if card.get("face_down")
        ),
        my_goat_tokens=sum(1 for card in my_monsters if base_name(card.get("name")) == TOKEN_NAME),
        opponent_goat_tokens=sum(
            1 for card in their_monsters if base_name(card.get("name")) == TOKEN_NAME
        ),
        lights_in_my_grave=sum(1 for value in attributes if value == "LIGHT"),
        darks_in_my_grave=sum(1 for value in attributes if value == "DARK"),
        my_best_attack=best_mine,
        opponent_best_attack=max(their_attacks, default=0),
        known_opponent_cards=tuple(sorted(name for name in known if name)),
        premium_removal_used_by_opponent=tuple(
            sorted(
                {
                    base_name(card.get("name"))
                    for card in _named(them.get("graveyard"))
                    if base_name(card.get("name")) in PREMIUM_REMOVAL
                }
            )
        ),
        estimated_battle_damage=damage,
        is_my_turn=observation.get("turn_player") == "you",
        phase=str(observation.get("phase") or ""),
        turn=int(observation.get("turn") or 0),
    )
