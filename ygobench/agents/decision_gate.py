"""Decide which decisions actually deserve a language model.

`select_chain` alone is ~78% of every decision in a GOAT duel, and the
overwhelming majority of those windows are forced or have nothing worth
choosing.  Sending them all to a model would burn tokens and latency on
non-decisions, and would make a thousand-game benchmark impractical.

This gate is a *filter*, never a player: when it resolves a decision
automatically it picks the only meaningful option available, and it records why.
Anything with two strategically distinct options goes to the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ygobench.agents.semantics import SemanticAction, SemanticDecision

#: Actions that never change the game on their own, so a prompt offering only
#: these plus one real option is not a strategic choice.
_INERT = {"end_turn", "shuffle_hand", "decline", "cancel_selection", "finish_selection"}

#: Prompts that ask "which card", where a single candidate leaves nothing to pick.
_SELECTION_PROMPTS = {"select_card", "select_tribute", "select_unselect_card"}


@dataclass(frozen=True)
class GateVerdict:
    """Whether a model is needed, and if not, which action to take and why."""

    needs_model: bool
    action: SemanticAction | None = None
    reason: str = ""

    @property
    def decision_source(self) -> str:
        return "llm" if self.needs_model else "automatic"


def _distinct_options(semantic: SemanticDecision) -> list[SemanticAction]:
    """Actions that represent a real, separable choice."""

    return [action for action in semantic.actions if action.name not in _INERT]


def classify(semantic: SemanticDecision, decision: dict[str, Any]) -> GateVerdict:
    """Classify one prompt as automatic or model-worthy."""

    actions = semantic.actions
    if not actions:
        return GateVerdict(needs_model=False, reason="no legal actions")

    if len(actions) == 1:
        return GateVerdict(
            needs_model=False, action=actions[0], reason="only one legal action"
        )

    meaningful = _distinct_options(semantic)

    # A chain window where nothing can actually be activated is a forced pass.
    if semantic.responder == "select_chain" and not meaningful:
        decline = next((a for a in actions if a.is_decline), actions[0])
        return GateVerdict(
            needs_model=False, action=decline, reason="chain window with nothing to activate"
        )

    # Every real option is gone: the only choices are inert, so take the decline.
    if not meaningful:
        decline = next((a for a in actions if a.is_decline), actions[0])
        return GateVerdict(
            needs_model=False, action=decline, reason="no non-trivial option available"
        )

    # Selection prompts ask *which card*, so a single candidate is not a choice.
    # The remaining option is to cancel, which abandons a cost already committed
    # to -- not something worth a model call, and actively harmful when taken.
    if semantic.responder in _SELECTION_PROMPTS and len(meaningful) == 1:
        return GateVerdict(
            needs_model=False,
            action=meaningful[0],
            reason="single candidate for a mandatory selection",
        )

    # Elsewhere, one real option among declines is still a genuine take-it-or-not
    # choice, unless nothing but that option exists.
    if len(meaningful) == 1 and len(meaningful) == len(actions):
        return GateVerdict(
            needs_model=False,
            action=meaningful[0],
            reason="single valid completion of a mandatory selection",
        )

    if semantic.responder in {"select_place", "select_position"} and len(actions) > 1:
        # Zone choice is almost never strategically load-bearing in GOAT and it
        # is very common; spending a model call on it is not worth the tokens.
        return GateVerdict(
            needs_model=False,
            action=meaningful[0],
            reason=f"{semantic.responder} is not strategically distinct in GOAT",
        )

    return GateVerdict(needs_model=True, reason=f"{len(meaningful)} strategically distinct options")
