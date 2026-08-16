"""Deterministic baseline that prefers to act rather than decline.

``PassiveAgent`` always takes the engine's passive response, so it declines
almost everything and exercises very little of the game.  This agent picks the
first *concrete* action instead, using the semantic layer to recognise which
entries are genuine declines.  It is still fully deterministic -- no RNG -- so
it is useful for reproducibility checks that also drive real game states.
"""

from __future__ import annotations

from ygobench.agents.base import BaseAgent
from ygobench.agents.semantics import translate
from ygobench.engine.protocol import ActionChoice, DecisionRequest


class FirstLegalAgent(BaseAgent):
    name = "first_legal"

    def predict(self, decision: DecisionRequest) -> ActionChoice:
        if not decision.legal_actions:
            raise ValueError("No legal actions are available")

        semantic = translate(
            decision.observation.get("decision", {}) or {}, decision.legal_actions
        )
        for action in semantic.actions:
            if action.is_decline:
                continue
            # Ending the turn is technically an action, but choosing it first
            # would make every turn a no-op.
            if action.name in {"end_turn", "shuffle_hand"}:
                continue
            return action.raw
        return decision.legal_actions[0]
