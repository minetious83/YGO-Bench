"""A semantic naming layer over the engine's raw action vocabulary.

ocgcore's prompt vocabulary is faithful to the engine, not to how players talk
about the game: a Flip Summon arrives as ``repos``, an attack declaration as a
bare ``attack`` command, and a cost is paid by toggling indices in a
select/unselect list.  Asking a model to reason about "Flip Summon Tsukuyomi"
while the only legal command is called ``repos`` is needless friction, and it
contaminates any later judgement of how well the model actually plays.

This module is a **translation only**.  It renames and describes what the engine
already offers; it never invents an action, never widens what is legal, and
never adds information the acting player is not entitled to.  The engine stays
the sole legality oracle:

* every semantic action wraps exactly one ``ActionChoice`` from
  ``legal_actions_from_pending``, kept verbatim in ``raw``,
* the ordering of the underlying action set is preserved,
* names and arguments are derived only from the observation the acting player
  already receives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ygobench.engine.protocol import ActionChoice

#: Engine idle/battle command -> the verb a player would use.
COMMAND_VERBS: dict[str, str] = {
    "summon": "normal_summon",
    "sp_summon": "special_summon",
    "set_monster": "set_monster",
    "set_spell": "set_spell_trap",
    "activate": "activate",
    "attack": "declare_attack",
    "to_battle_phase": "enter_battle_phase",
    "to_main_phase_2": "enter_main_phase_2",
    "to_main2": "enter_main_phase_2",
    "to_end_phase": "end_turn",
    "shuffle_hand": "shuffle_hand",
}

#: Responders whose passive entry is a genuine decline rather than a real choice.
_TRUE_DECLINE = {"select_chain", "select_effectyn", "select_yesno"}


@dataclass(frozen=True)
class SemanticAction:
    """One engine action, described the way a player would describe it."""

    name: str
    arguments: dict[str, Any]
    description: str
    raw: ActionChoice
    index: int
    is_decline: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Debug/replay form: the semantic view plus the exact engine payload."""

        return {
            "index": self.index,
            "name": self.name,
            "arguments": self.arguments,
            "description": self.description,
            "is_decline": self.is_decline,
            "raw": {"tool": self.raw.tool, "arguments": self.raw.arguments},
        }


@dataclass(frozen=True)
class SemanticDecision:
    """The whole prompt, translated."""

    responder: str
    prompt: str
    actions: tuple[SemanticAction, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "responder": self.responder,
            "prompt": self.prompt,
            "actions": [action.to_dict() for action in self.actions],
        }


def _card_name(card: Any) -> str | None:
    if isinstance(card, dict):
        name = card.get("name")
        if name:
            return str(name)
    return None


def _repos_name(card: dict[str, Any] | None) -> tuple[str, str]:
    """A reposition is a Flip Summon when the monster is face-down."""

    if card and card.get("face_down"):
        return "flip_summon", "Flip Summon"
    return "change_position", "Change battle position of"


def _idle_action(choice: dict[str, Any], raw: ActionChoice, index: int) -> SemanticAction:
    command = str(choice.get("command", ""))
    card = choice.get("card") if isinstance(choice.get("card"), dict) else None
    name = _card_name(card)

    if command == "repos":
        verb, phrase = _repos_name(card)
        description = f"{phrase} {name}" if name else phrase
    else:
        verb = COMMAND_VERBS.get(command, command)
        description = f"{verb.replace('_', ' ')} {name}".strip() if name else verb.replace("_", " ")

    arguments: dict[str, Any] = {}
    if name:
        arguments["card"] = name
    return SemanticAction(
        name=verb,
        arguments=arguments,
        description=description,
        raw=raw,
        index=index,
    )


def translate(
    decision: dict[str, Any], actions: tuple[ActionChoice, ...]
) -> SemanticDecision:
    """Describe an engine prompt and its legal actions in player vocabulary.

    ``decision`` is the acting player's own ``observation["decision"]`` and
    ``actions`` the matching ``legal_actions_from_pending`` output, so nothing
    here can see more than the agent already can.
    """

    responder = str(decision.get("responder", ""))
    cards = decision.get("cards", []) or []
    choices = decision.get("choices", []) or []
    selectable = decision.get("selectable_cards", []) or []
    selected = decision.get("selected_cards", []) or []
    combined = [*selectable, *selected]

    semantic: list[SemanticAction] = []
    for index, raw in enumerate(actions):
        arguments = raw.arguments
        is_decline = False
        name = responder
        description = raw.label or responder
        args: dict[str, Any] = {}

        if responder in {"select_idlecmd", "select_battlecmd"}:
            match = next(
                (
                    choice
                    for choice in choices
                    if choice.get("command") == arguments.get("command")
                    and choice.get("index") == arguments.get("index")
                ),
                None,
            )
            if match is not None:
                semantic.append(_idle_action(match, raw, index))
                continue

        elif responder == "select_chain":
            position = arguments.get("index")
            if position is None:
                name, description, is_decline = "decline", "Do not respond", True
            else:
                card_name = _card_name(cards[position]) if position < len(cards) else None
                name = "activate_in_chain"
                args = {"card": card_name} if card_name else {}
                description = f"Activate {card_name} in response" if card_name else "Activate"

        elif responder in {"select_effectyn", "select_yesno"}:
            accept = bool(arguments.get("accept"))
            name = "accept_effect" if accept else "decline"
            is_decline = not accept
            description = "Apply the optional effect" if accept else "Decline the optional effect"

        elif responder == "select_unselect_card":
            position = arguments.get("index")
            if position is None:
                # index None is "finish" only once something is selected; with
                # an empty selection it cancels the effect outright.  Naming
                # both "finish" lets an agent abort a cost it just chose to pay
                # and loop forever re-choosing it.
                if selected:
                    name, description = "finish_selection", "Finish selecting"
                else:
                    name, description = "cancel_selection", "Cancel without selecting"
                    is_decline = True
            elif position < len(selectable):
                card_name = _card_name(combined[position])
                name, args = "select_card", {"card": card_name} if card_name else {}
                description = f"Select {card_name}" if card_name else "Select a card"
            else:
                card_name = _card_name(combined[position]) if position < len(combined) else None
                name, args = "unselect_card", {"card": card_name} if card_name else {}
                description = f"Unselect {card_name}" if card_name else "Unselect a card"

        elif responder in {"select_card", "select_tribute"}:
            if arguments.get("cancel"):
                name, description, is_decline = "cancel", "Cancel the selection", True
            else:
                names = [
                    _card_name(cards[i]) for i in arguments.get("indices", []) if i < len(cards)
                ]
                names = [n for n in names if n]
                name = "choose_cards"
                args = {"cards": names}
                description = f"Choose {', '.join(names)}" if names else "Choose"

        elif responder == "select_position":
            position = str(arguments.get("position", ""))
            name, args = "choose_position", {"position": position}
            description = f"Place in {position.replace('_', ' ')}"

        elif responder == "select_place":
            name, args = "choose_zone", {"places": arguments.get("places", [])}
            description = "Choose a zone"

        elif responder == "select_option":
            name, args = "choose_option", {"option": raw.label}
            description = f"Choose option: {raw.label}"

        semantic.append(
            SemanticAction(
                name=name,
                arguments=args,
                description=description,
                raw=raw,
                index=index,
                is_decline=is_decline,
            )
        )

    return SemanticDecision(
        responder=responder,
        prompt=_PROMPTS.get(responder, responder.replace("_", " ")),
        actions=tuple(semantic),
    )


_PROMPTS = {
    "select_idlecmd": "Choose an action for your Main Phase",
    "select_battlecmd": "Choose an action for your Battle Phase",
    "select_chain": "Respond to the current chain, or decline",
    "select_effectyn": "Apply this optional effect?",
    "select_yesno": "Answer yes or no",
    "select_card": "Choose card(s)",
    "select_tribute": "Choose card(s) to Tribute",
    "select_unselect_card": "Select or unselect cards until the requirement is met",
    "select_position": "Choose a battle position",
    "select_place": "Choose a zone",
    "select_option": "Choose one of the card's options",
}


def has_true_decline(decision: SemanticDecision) -> bool:
    """Whether declining is genuinely available at this prompt.

    Only some prompts offer a real "do nothing".  A select/unselect prompt in
    the middle of paying a cost, for example, does not.
    """

    return decision.responder in _TRUE_DECLINE and any(a.is_decline for a in decision.actions)
