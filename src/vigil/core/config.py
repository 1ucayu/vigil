"""Pydantic configuration models and YAML loader.

Provides typed, validated configuration for all Vigil components.
Loads from YAML files (configs/default.yaml + per-app overrides).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    """Configuration for app exploration."""

    max_exploration_steps: int = 500
    screenshot_format: str = "png"
    exploration_strategy: Literal["bfs", "dfs", "hybrid"] = "bfs"
    exploration_backend: Literal["native", "ape"] = "native"


class ApeConfig(BaseModel):
    """Configuration for APE exploration backend."""

    jar_path: str = "libs/ape.jar"
    device_jar_path: str = "/data/local/tmp/ape.jar"
    device_output_dir: str = "/sdcard/ape-output"
    running_minutes: int = 10
    ape_mode: Literal["sata", "random"] = "sata"


class LLMConfig(BaseModel):
    """Configuration for LLM client (offline stage only)."""

    provider: Literal["anthropic", "openai", "google"] = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.0


class StateAbstractionConfig(BaseModel):
    """Configuration for state abstraction (Stage 2)."""

    similarity_threshold: float = 0.85
    use_llm_fallback: bool = True


class VerificationConfig(BaseModel):
    """Configuration for FSM verification and replay."""

    confidence_threshold: float = 0.7
    replay_trials: int = 3
    max_path_length: int = 10


class RuntimeConfig(BaseModel):
    """Configuration for the online runtime verifier."""

    latency_budget_ms: int = 25
    fallback_on_uncertain: Literal["user", "llm", "deny"] = "user"


class EvolutionConfig(BaseModel):
    """Configuration for Tier 3 online micro-evolution."""

    enable_tier3: bool = True
    similarity_threshold_inherit: float = 0.80
    max_evolution_cache_size: int = 1000
    evolution_log_path: str = "data/evolution_log.jsonl"


class VigilConfig(BaseModel):
    """Root configuration model for Vigil.

    Loads from configs/default.yaml with optional per-app overrides.
    """

    app: AppConfig = Field(default_factory=AppConfig)
    ape: ApeConfig = Field(default_factory=ApeConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    state_abstraction: StateAbstractionConfig = Field(default_factory=StateAbstractionConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    evolution: EvolutionConfig = Field(default_factory=EvolutionConfig)

    @classmethod
    def from_yaml(cls, path: str | Path, override_path: str | Path | None = None) -> VigilConfig:
        """Load configuration from a YAML file with optional per-app override.

        Args:
            path: Path to the base config YAML (e.g., configs/default.yaml).
            override_path: Optional path to a per-app override YAML.

        Returns:
            Merged VigilConfig instance.
        """
        path = Path(path)
        data = yaml.safe_load(path.read_text()) or {}

        if override_path:
            override_path = Path(override_path)
            if override_path.exists():
                override_data = yaml.safe_load(override_path.read_text()) or {}
                data = _deep_merge(data, override_data)

        return cls(**data)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override dict into base dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
