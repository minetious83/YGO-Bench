"""`goat_llm_v0_1` -- the first language-model GOAT strategy agent.

It consumes exactly what `goat_heuristic_v1` consumes -- the acting player's
observation, the derived strategic state and the semantic legal actions -- and
returns exactly one of those actions.  The two differ only in how they rank
choices.

Two things keep it honest:

* **The model never sees hidden information.**  The prompt is built from the
  player-visible observation alone; opponent hand cards appear only as a count.
* **The model never names a game command.**  Every legal action is given a
  stable id (``A0``, ``A1``, ...) and the model returns one of those ids.  An id
  that does not exist is rejected rather than coerced into something else.

Most decisions never reach the model at all: see :mod:`ygobench.agents.decision_gate`.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from ygobench.agents.base import BaseAgent
from ygobench.agents.decision_gate import GateVerdict, classify
from ygobench.agents.semantics import SemanticAction, SemanticDecision, translate
from ygobench.engine.protocol import ActionChoice, DecisionRequest
from ygobench.goat.strategy_state import GoatStrategicState, extract

AGENT_VERSION = "goat_llm_v0_1"
PROMPT_VERSION = "goat_llm_prompt_v1"

SYSTEM_PROMPT = """You are playing Yu-Gi-Oh! in the GOAT format (April 2005 TCG).

Rules of engagement:
- Choose exactly one action from the numbered list you are given. Nothing else is legal.
- Project Ignis / ocgcore is the sole authority on legality and rulings. Do not
  reason about what "should" be legal, and do not invent rulings; if an action is
  not listed, it is not available.
- You cannot see your opponent's hand or their face-down cards. Do not guess at
  specific hidden cards; play around what is plausible instead.

Play to win the duel. Weigh card advantage, tempo, life totals, lethal lines,
and what your opponent may be representing. Premium cards (Heavy Storm, Mirror
Force, Torrential Tribute, Ring of Destruction, Chaos monsters) are scarce -
spend them when they gain real value, not merely because they are legal.

Reply with JSON only, in exactly this form:
{"action_id": "A3", "why": "one short sentence"}"""


class ModelProvider(Protocol):
    """Anything that can turn a prompt into text."""

    def respond(self, *, system: str, user: str) -> ModelReply: ...


@dataclass
class ModelReply:
    text: str
    model: str = "unknown"
    usage: dict[str, int] = field(default_factory=dict)
    latency_seconds: float = 0.0


class LLMDecisionError(RuntimeError):
    """The model failed to name a usable action; never silently substituted."""


_ACTION_ID = re.compile(r"\bA(\d+)\b")


def action_id(index: int) -> str:
    return f"A{index}"


def render_state(state: GoatStrategicState) -> str:
    return "\n".join(
        [
            f"  life: you {state.my_lp} / opponent {state.opponent_lp}",
            f"  hand: you {state.my_hand_size} /"
            f" opponent {state.opponent_hand_size} (unknown cards)",
            f"  monsters: you {state.my_monster_count} / opponent {state.opponent_monster_count}"
            f" (their face-down: {state.opponent_face_down_monsters})",
            f"  backrow: you {state.my_backrow_count} / opponent {state.opponent_backrow_count}",
            f"  goat tokens: you {state.my_goat_tokens} / opponent {state.opponent_goat_tokens}",
            f"  your graveyard: {state.lights_in_my_grave} LIGHT, {state.darks_in_my_grave} DARK"
            f" (chaos cost available: {'yes' if state.chaos_ready else 'no'})",
            f"  best attack: you {state.my_best_attack} / opponent {state.opponent_best_attack}",
            f"  estimated battle damage available: {state.estimated_battle_damage}",
        ]
    )


def _visible_cards(zone: list[dict[str, Any]] | None) -> str:
    named = []
    hidden = 0
    for card in zone or []:
        if not card:
            continue
        if card.get("name"):
            attack = card.get("attack")
            suffix = f" [{attack} ATK]" if isinstance(attack, int) and attack else ""
            named.append(f"{card['name']}{suffix}")
        else:
            hidden += 1
    if hidden:
        named.append(f"{hidden} face-down")
    return ", ".join(named) if named else "empty"


def build_prompt(
    observation: dict[str, Any], state: GoatStrategicState, semantic: SemanticDecision
) -> str:
    """Render the player-visible position and the numbered legal actions.

    Only fields the acting player is entitled to are used; the opponent's hand
    contributes a count and nothing else.
    """

    me = observation.get("you", {}) or {}
    them = observation.get("opponent", {}) or {}

    lines = [
        "format: GOAT 2005",
        f"turn {state.turn}, phase {state.phase},"
        f" {'your turn' if state.is_my_turn else 'opponent turn'}",
        "",
        "position:",
        render_state(state),
        "",
        f"your hand: {_visible_cards(me.get('hand'))}",
        f"your monsters: {_visible_cards(me.get('monster_zone'))}",
        f"your spells/traps: {_visible_cards(me.get('spell_trap_zone'))}",
        f"opponent monsters: {_visible_cards(them.get('monster_zone'))}",
        f"opponent spells/traps: {_visible_cards(them.get('spell_trap_zone'))}",
        f"your graveyard: {_visible_cards(me.get('graveyard'))}",
        f"opponent graveyard: {_visible_cards(them.get('graveyard'))}",
    ]
    if state.known_opponent_cards:
        lines.append(f"opponent cards you have seen: {', '.join(state.known_opponent_cards)}")
    lines += ["", semantic.prompt + ":"]
    lines += [
        f"  {action_id(index)}: {action.description}"
        for index, action in enumerate(semantic.actions)
    ]
    lines += ["", "Reply with JSON: {\"action_id\": \"...\", \"why\": \"...\"}"]
    return "\n".join(lines)


def parse_reply(text: str, count: int) -> tuple[int, str]:
    """Extract a valid action index, or raise.

    Never falls back to a different action: an unusable reply is an error.
    """

    payload: dict[str, Any] | None = None
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if match:
        try:
            candidate = json.loads(match.group(0))
            if isinstance(candidate, dict):
                payload = candidate
        except json.JSONDecodeError:
            payload = None

    raw_id = str(payload.get("action_id", "")) if payload else ""
    why = str(payload.get("why", "")) if payload else ""

    identifier = _ACTION_ID.search(raw_id) or (None if payload else _ACTION_ID.search(text or ""))
    if identifier is None:
        raise LLMDecisionError(f"no action id in model reply: {text!r:.200}")

    index = int(identifier.group(1))
    if not 0 <= index < count:
        raise LLMDecisionError(f"action id A{index} is not one of the {count} available actions")
    return index, why.strip()[:200]


class GoatLLMAgent(BaseAgent):
    name = AGENT_VERSION

    def __init__(
        self,
        provider: ModelProvider,
        *,
        max_retries: int = 1,
        temperature: float = 0.0,
        model_id: str = "unknown",
    ) -> None:
        self.provider = provider
        self.max_retries = max_retries
        self.temperature = temperature
        self.model_id = model_id
        self.last_trace: dict[str, Any] = {}
        self.model_calls = 0
        self.automatic_decisions = 0
        self.retries = 0
        self.malformed = 0
        self.usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

    def reset(self) -> None:
        self.last_trace = {}
        self.model_calls = 0
        self.automatic_decisions = 0
        self.retries = 0
        self.malformed = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    # -- decisions ---------------------------------------------------------

    def predict(self, request: DecisionRequest) -> ActionChoice:
        if not request.legal_actions:
            raise ValueError("No legal actions are available")

        decision = request.observation.get("decision", {}) or {}
        semantic = translate(decision, request.legal_actions)
        verdict = classify(semantic, decision)

        if not verdict.needs_model:
            return self._automatic(semantic, verdict)
        return self._ask_model(request, semantic, verdict)

    def _automatic(self, semantic: SemanticDecision, verdict: GateVerdict) -> ActionChoice:
        action = verdict.action or semantic.actions[0]
        self.automatic_decisions += 1
        self.last_trace = {
            "agent": AGENT_VERSION,
            "decision_source": "automatic",
            "automatic_reason": verdict.reason,
            "responder": semantic.responder,
            "chosen_action": action.to_dict(),
        }
        return action.raw

    def _ask_model(
        self, request: DecisionRequest, semantic: SemanticDecision, verdict: GateVerdict
    ) -> ActionChoice:
        state = extract(request.observation)
        prompt = build_prompt(request.observation, state, semantic)
        candidates = [action_id(i) for i in range(len(semantic.actions))]

        errors: list[str] = []
        chosen: SemanticAction | None = None
        why = ""
        latency = 0.0
        reply: ModelReply | None = None

        for attempt in range(self.max_retries + 1):
            user = prompt
            if attempt:
                self.retries += 1
                user = (
                    f"{prompt}\n\nYour previous reply could not be used ({errors[-1]}). "
                    f"Reply with JSON naming one of: {', '.join(candidates)}"
                )
            started = time.perf_counter()
            reply = self.provider.respond(system=SYSTEM_PROMPT, user=user)
            latency += reply.latency_seconds or (time.perf_counter() - started)
            self.model_calls += 1
            for key in ("input_tokens", "output_tokens"):
                self.usage[key] = self.usage.get(key, 0) + int(reply.usage.get(key, 0) or 0)
            try:
                index, why = parse_reply(reply.text, len(semantic.actions))
                chosen = semantic.actions[index]
                break
            except LLMDecisionError as exc:
                self.malformed += 1
                errors.append(str(exc))

        self.last_trace = {
            "agent": AGENT_VERSION,
            "decision_source": "llm",
            "responder": semantic.responder,
            "prompt_version": PROMPT_VERSION,
            "model": reply.model if reply else self.model_id,
            "temperature": self.temperature,
            "candidate_action_ids": candidates,
            "chosen_action": chosen.to_dict() if chosen else None,
            "action_id": action_id(chosen.index) if chosen else None,
            "rationale": why,
            "latency_seconds": round(latency, 4),
            "usage": dict(reply.usage) if reply else {},
            "parse_errors": errors,
            "retries": len(errors),
            "gate_reason": verdict.reason,
        }

        if chosen is None:
            # No silent substitution: the duel runner records this as an agent
            # failure rather than quietly playing something else.
            raise LLMDecisionError(
                "model did not name a usable action after "
                f"{self.max_retries + 1} attempts: {errors}"
            )
        return chosen.raw

    @property
    def provider_config(self) -> dict[str, Any]:
        return {
            "name": AGENT_VERSION,
            "agent_version": AGENT_VERSION,
            "prompt_version": PROMPT_VERSION,
            "model": self.model_id,
            "temperature": self.temperature,
            "retry_policy": f"{self.max_retries} retry on malformed output",
        }


class UpstreamProvider:
    """Adapter onto the pinned yugi-bench provider layer.

    Kept separate from :class:`GoatLLMAgent` so the agent itself stays testable
    without any provider, network or credentials.
    """

    def __init__(self, model_config, **kwargs: Any) -> None:
        from ygobench.agents.llm_agent import _provider_runtime

        _, get_provider = _provider_runtime()
        self._provider = get_provider(model_config.provider, model_config.model, **kwargs)
        self.model_id = model_config.model

    def respond(self, *, system: str, user: str) -> ModelReply:
        turn = self._provider.respond(
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[],
        )
        return ModelReply(
            text=getattr(turn, "text", "") or "",
            model=self.model_id,
            usage=dict(getattr(turn, "usage", {}) or {}),
            latency_seconds=float(getattr(turn, "wallclock_seconds", 0.0) or 0.0),
        )
