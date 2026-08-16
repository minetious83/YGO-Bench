"""`goat_heuristic_v1` -- a transparent, deterministic GOAT baseline.

The point of this agent is to be *understandable*, not to be strong.  It scores
every semantic legal action with a small set of named rules, picks the highest
score, and records why.  Twenty-odd explainable heuristics beat three hundred
card-specific branches nobody can maintain.

It is bound by the same contract a future LLM agent will be:

* it sees only the acting player's observation, the derived strategic state and
  the semantic legal actions,
* it returns exactly one of those actions,
* it never consults omniscient state and never invents legality.

Card-specific handling is limited to genuinely format-defining cards; everything
else is scored by generic tactical, economy and tempo rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ygobench.agents.base import BaseAgent
from ygobench.agents.semantics import SemanticAction, SemanticDecision, translate
from ygobench.engine.protocol import ActionChoice, DecisionRequest
from ygobench.goat.strategy_state import (
    CHAOS_MONSTERS,
    DRAW_SPELLS,
    PREMIUM_REMOVAL,
    TOKEN_NAME,
    GoatStrategicState,
    base_name,
    extract,
)

AGENT_VERSION = "goat_heuristic_v1"

#: Rough card-quality tiers, used when choosing what to give up or hand over.
CHEAP_TO_LOSE = {TOKEN_NAME}

#: Base priority per semantic action kind: acting beats passing, but paying a
#: cost you just committed to beats everything except lethal.
BASE_PRIORITY: dict[str, float] = {
    "special_summon": 1.5,
    "declare_attack": 1.2,
    "normal_summon": 1.0,
    "activate": 0.8,
    "activate_in_chain": 0.6,
    "accept_effect": 0.5,
    "flip_summon": 0.5,
    "set_monster": 0.4,
    "set_spell_trap": 0.3,
    "change_position": 0.0,
    "choose_option": 0.0,
    "shuffle_hand": -0.8,
    "end_turn": -1.0,
    "decline": -0.3,
    "cancel_selection": -8.0,
}


@dataclass(frozen=True)
class Contribution:
    rule: str
    delta: float


@dataclass
class ScoredAction:
    action: SemanticAction
    contributions: list[Contribution] = field(default_factory=list)

    @property
    def score(self) -> float:
        return round(sum(c.delta for c in self.contributions), 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.action.name,
            "arguments": self.action.arguments,
            "description": self.action.description,
            "score": self.score,
            "contributions": [{"rule": c.rule, "delta": c.delta} for c in self.contributions],
            "raw": {"tool": self.action.raw.tool, "arguments": self.action.raw.arguments},
        }


def _card(action: SemanticAction) -> str:
    return base_name(action.arguments.get("card"))


def _cards(action: SemanticAction) -> list[str]:
    return [base_name(name) for name in action.arguments.get("cards", []) if name]


# --------------------------------------------------------------------------
# Rules.  Each returns (rule_name, delta) pairs and may return nothing.
# --------------------------------------------------------------------------


def rule_base_priority(action, state, decision):
    yield "base_priority", BASE_PRIORITY.get(action.name, 0.0)


def rule_lethal(action, state, decision):
    """Swing for game when the opponent cannot block it."""

    if action.name == "declare_attack" and state.opponent_field_open:
        if state.estimated_battle_damage >= state.opponent_lp > 0:
            yield "lethal_on_board", 100.0


def rule_favourable_battle(action, state, decision):
    if action.name != "declare_attack":
        return
    if state.opponent_field_open:
        yield "direct_attack", 1.0 + state.estimated_battle_damage / 2000
    elif state.my_best_attack > state.opponent_best_attack:
        yield "attacker_outclasses_defender", 1.5
    elif state.my_best_attack < state.opponent_best_attack:
        # Attacking into a bigger monster is how a naive agent loses material.
        yield "suicidal_attack", -3.0


def rule_draw_spells(action, state, decision):
    """Pot of Greed and Graceful Charity are free card advantage."""

    if action.name == "activate" and _card(action) in DRAW_SPELLS:
        yield "free_card_advantage", 2.5


def rule_premium_removal_scarcity(action, state, decision):
    """Mass removal is scarce; spending it needs a reason."""

    name = _card(action)
    if action.name in {"activate", "activate_in_chain"} and name in PREMIUM_REMOVAL:
        yield "premium_resource", -2.5


def rule_heavy_storm(action, state, decision):
    """Value scales with their backrow and is paid for out of your own."""

    if action.name not in {"activate", "activate_in_chain"} or _card(action) != "Heavy Storm":
        return
    yield "storm_opposing_backrow", 2.0 * state.opponent_backrow_count
    yield "storm_own_backrow", -1.0 * state.my_backrow_count
    if state.opponent_backrow_count == 0:
        yield "storm_no_targets", -4.0


def rule_targeted_backrow_removal(action, state, decision):
    if action.name not in {"activate", "activate_in_chain"}:
        return
    if _card(action) not in {"Mystical Space Typhoon", "Dust Tornado"}:
        return
    if state.opponent_backrow_count:
        yield "spot_removal_has_target", 1.0
    else:
        yield "spot_removal_no_target", -2.5


def rule_mass_monster_removal(action, state, decision):
    """Mirror Force / Torrential are worth their scarcity only on a real board."""

    if action.name not in {"activate", "activate_in_chain"}:
        return
    if _card(action) not in {"Mirror Force", "Torrential Tribute"}:
        return
    yield "sweeper_value", 1.6 * state.opponent_monster_count
    if state.opponent_monster_count <= 1:
        yield "sweeper_low_value", -1.5


def rule_ring_of_destruction(action, state, decision):
    """Symmetrical damage: fine when ahead on life, bad when behind."""

    if action.name not in {"activate", "activate_in_chain"}:
        return
    if _card(action) != "Ring of Destruction":
        return
    if state.opponent_best_attack >= state.my_lp:
        yield "ring_would_kill_me", -6.0
    elif state.opponent_monster_count:
        yield "ring_removes_threat", 1.5 + state.opponent_best_attack / 2000


def rule_nobleman_of_crossout(action, state, decision):
    if action.name not in {"activate", "activate_in_chain"}:
        return
    if _card(action) != "Nobleman of Crossout":
        return
    if state.opponent_face_down_monsters:
        yield "nobleman_has_face_down_target", 2.5
    else:
        yield "nobleman_no_target", -3.0


def rule_chaos_summon(action, state, decision):
    """Chaos monsters are the format's payoff when the cost is naturally met."""

    if action.name != "special_summon" or _card(action) not in CHAOS_MONSTERS:
        return
    if state.chaos_ready:
        yield "chaos_cost_available", 2.0
        if state.board_deficit > 0:
            yield "chaos_stabilises_board", 1.5


def rule_scapegoat(action, state, decision):
    """Tokens are defence, and they are the Metamorphosis engine."""

    if action.name != "activate" or _card(action) != "Scapegoat":
        return
    if state.board_deficit > 0:
        yield "goats_stabilise", 1.5
    if state.my_monster_count >= 3:
        yield "goats_redundant", -1.5


def rule_metamorphosis(action, state, decision):
    if action.name != "activate" or _card(action) != "Metamorphosis":
        return
    if state.my_goat_tokens:
        yield "metamorphosis_on_token", 2.5
    elif state.my_monster_count == 0:
        yield "metamorphosis_no_material", -3.0


def rule_thousand_eyes_equip(action, state, decision):
    """TER's equip is optional -- declining it throws the card away."""

    if state.opponent_monster_count == 0:
        return
    if action.name == "activate_in_chain" and "Thousand-Eyes" in (
        action.arguments.get("card") or ""
    ):
        yield "ter_steal_available", 3.0
    if action.name == "accept_effect":
        yield "optional_effect_with_target", 0.8


def rule_never_cancel_a_committed_cost(action, state, decision):
    """Cancelling a cost you just chose is how an agent loops forever."""

    if action.name == "cancel_selection":
        yield "abandons_committed_cost", -4.0


def rule_prefer_cheap_giveaway(action, state, decision):
    """When handing over or paying with your own cards, spend the cheapest.

    Creature Swap is the motivating case: giving away a Sheep Token instead of a
    real monster is close to free.
    """

    if action.name != "choose_cards":
        return
    names = _cards(action)
    if not names:
        return
    owners = {
        card.get("owner")
        for card in (decision.get("cards") or [])
        if base_name(card.get("name")) in names
    }
    if owners == {"you"}:
        if all(name in CHEAP_TO_LOSE for name in names):
            yield "gives_up_only_tokens", 1.5
        else:
            yield "gives_up_real_cards", -0.5 * len(names)
    elif owners == {"opponent"}:
        # Choosing among their cards: take the biggest threat.
        best = max(
            (
                int(card.get("attack") or 0)
                for card in (decision.get("cards") or [])
                if base_name(card.get("name")) in names
            ),
            default=0,
        )
        yield "removes_largest_threat", best / 2000


def rule_avoid_overextension(action, state, decision):
    """Do not walk a fourth monster into a board that screams sweeper."""

    if action.name not in {"normal_summon", "special_summon"}:
        return
    if state.my_monster_count >= 3 and state.opponent_backrow_count >= 1:
        yield "overextends_into_backrow", -1.2


def rule_develop_when_behind(action, state, decision):
    if action.name in {"normal_summon", "special_summon"} and state.board_deficit > 0:
        yield "develops_while_behind", 0.8


def rule_hold_backrow_when_empty_board(action, state, decision):
    if action.name == "set_spell_trap" and state.my_backrow_count >= 4:
        yield "backrow_full", -1.0


def rule_flip_summon_value(action, state, decision):
    if action.name == "flip_summon" and state.opponent_monster_count:
        yield "flip_effect_pressure", 0.6


def rule_known_information(action, state, decision):
    """Mild bonus for acting when we know what they are holding."""

    if action.name in {"activate", "normal_summon"} and state.known_opponent_cards:
        yield "acts_with_information", 0.1


def rule_end_turn_last_resort(action, state, decision):
    if action.name == "end_turn" and state.is_my_turn and state.my_hand_size > 0:
        yield "cards_left_to_use", -0.5


RULES = (
    rule_base_priority,
    rule_lethal,
    rule_favourable_battle,
    rule_draw_spells,
    rule_premium_removal_scarcity,
    rule_heavy_storm,
    rule_targeted_backrow_removal,
    rule_mass_monster_removal,
    rule_ring_of_destruction,
    rule_nobleman_of_crossout,
    rule_chaos_summon,
    rule_scapegoat,
    rule_metamorphosis,
    rule_thousand_eyes_equip,
    rule_never_cancel_a_committed_cost,
    rule_prefer_cheap_giveaway,
    rule_avoid_overextension,
    rule_develop_when_behind,
    rule_hold_backrow_when_empty_board,
    rule_flip_summon_value,
    rule_known_information,
    rule_end_turn_last_resort,
)


def score_actions(
    semantic: SemanticDecision,
    state: GoatStrategicState,
    decision: dict[str, Any],
) -> list[ScoredAction]:
    """Score every legal action, keeping each rule's contribution."""

    scored: list[ScoredAction] = []
    for action in semantic.actions:
        entry = ScoredAction(action=action)
        for rule in RULES:
            for name, delta in rule(action, state, decision) or ():
                if delta:
                    entry.contributions.append(Contribution(rule=name, delta=round(delta, 4)))
        scored.append(entry)
    return scored


class GoatHeuristicAgent(BaseAgent):
    """Deterministic, explainable GOAT baseline."""

    name = AGENT_VERSION

    def __init__(self) -> None:
        self.last_trace: dict[str, Any] = {}

    def reset(self) -> None:
        self.last_trace = {}

    def predict(self, request: DecisionRequest) -> ActionChoice:
        if not request.legal_actions:
            raise ValueError("No legal actions are available")

        decision = request.observation.get("decision", {}) or {}
        semantic = translate(decision, request.legal_actions)
        state = extract(request.observation)
        scored = score_actions(semantic, state, decision)

        # Highest score wins; ties break on the engine's own ordering, so the
        # choice is reproducible.
        best = max(range(len(scored)), key=lambda i: (scored[i].score, -i))
        chosen = scored[best]
        tie = [i for i, entry in enumerate(scored) if entry.score == chosen.score]

        self.last_trace = {
            "agent": AGENT_VERSION,
            "responder": semantic.responder,
            "strategic_state": state.to_dict(),
            "chosen_action": chosen.to_dict(),
            "candidate_actions": [entry.to_dict() for entry in scored],
            "tie_break": {
                "tied_indices": tie,
                "policy": "lowest engine action index",
                "chosen_index": best,
            },
        }
        return chosen.action.raw
