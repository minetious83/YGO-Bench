"""Create full-duel agents from stable CLI identifiers."""

from __future__ import annotations

from ygobench.agents.base import BaseAgent
from ygobench.agents.first_legal_agent import FirstLegalAgent
from ygobench.agents.goat_heuristic import GoatHeuristicAgent
from ygobench.agents.goat_llm import GoatLLMAgent, UpstreamProvider
from ygobench.agents.llm_agent import LLMFullDuelAgent
from ygobench.agents.passive_agent import PassiveAgent
from ygobench.agents.random_agent import RandomAgent
from ygobench.config import default_model_config, missing_api_key


def create_agent(agent_id: str, *, seed: int = 0) -> BaseAgent:
    if agent_id == "passive":
        return PassiveAgent()
    if agent_id == "random":
        return RandomAgent(seed=seed)
    if agent_id == "first_legal":
        return FirstLegalAgent()
    if agent_id in {"goat_heuristic", "goat_heuristic_v1"}:
        return GoatHeuristicAgent()
    if agent_id.startswith("goat_llm"):
        parts = agent_id.split(":", 2)
        provider = parts[1] if len(parts) > 1 and parts[1] else None
        model = parts[2] if len(parts) > 2 and parts[2] else None
        config = default_model_config(provider, model)
        missing = missing_api_key(config.provider)
        if missing:
            raise ValueError(
                f"{agent_id} needs {missing}; "
                "set it in .env or the shell before benchmarking."
            )
        return GoatLLMAgent(
            UpstreamProvider(config), temperature=0.0, model_id=config.model
        )
    if agent_id.startswith("react-fast"):
        parts = agent_id.split(":", 2)
        provider = parts[1] if len(parts) > 1 and parts[1] else None
        model = parts[2] if len(parts) > 2 and parts[2] else None
        return LLMFullDuelAgent(
            default_model_config(provider, model),
            max_tokens=32768,
            thinking_enabled=False,
            profile="react-fast",
        )
    if agent_id.startswith("react"):
        parts = agent_id.split(":", 2)
        provider = parts[1] if len(parts) > 1 and parts[1] else None
        model = parts[2] if len(parts) > 2 and parts[2] else None
        return LLMFullDuelAgent(default_model_config(provider, model))
    raise ValueError(
        f"Unknown full-duel agent {agent_id!r}; use passive, random, first_legal, "
        "goat_heuristic, goat_llm[:provider:model], react[:provider:model], "
        "or react-fast[:provider:model]."
    )
