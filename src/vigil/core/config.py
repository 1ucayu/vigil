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


class DeviceConfig(BaseModel):
    """Configuration for the target Android device.

    ``type`` is descriptive metadata that is logged and used to tag output
    artifacts (bundle directories, exploration traces). ``serial`` lets
    users pin a specific device when multiple are visible to ADB; when
    ``None``, the resolver in ``vigil.core.device_resolver`` picks one
    deterministically based on ``type``.

    Attributes:
        type: Device kind to target. ``"auto"`` accepts whatever single
            device is visible; ``"emulator"``/``"physical"`` filter the
            ADB device list before selection.
        serial: Explicit ADB serial. When set, bypasses the resolver
            entirely and trusts the value.
        profile_name: Suffix appended to data/bundle directories so that
            artifacts from different device profiles don't overwrite each
            other (e.g. ``emulator_pixel6a_api34``). Use ``"default"`` to
            keep the legacy non-suffixed paths.
    """

    type: Literal["emulator", "physical", "auto"] = "auto"
    serial: str | None = None
    profile_name: str = "default"


class ApeConfig(BaseModel):
    """Configuration for APE exploration backend."""

    jar_path: str = "libs/ape.jar"
    device_jar_path: str = "/data/local/tmp/ape.jar"
    device_output_dir: str = "/sdcard/ape-output"
    running_minutes: int = 10
    ape_mode: Literal["sata", "random"] = "sata"


PROXY_CHAT_MODELS: list[str] = [
    "claude-sonnet-4.6",
    "claude-opus-4.6",
    "claude-opus-4.6-1m",
    "claude-sonnet-4.5",
    "claude-opus-4.5",
    "claude-haiku-4.5",
    "claude-sonnet-4",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5-mini",
    "gpt-4.1",
    "gpt-4o",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
]


class LLMConfig(BaseModel):
    """Configuration for LLM client (offline stage only)."""

    provider: Literal["anthropic", "openai", "google", "proxy"] = "proxy"
    model: str = "claude-sonnet-4.6"
    temperature: float = 0.0
    openai_base_url: str = "http://localhost:4141/v1"
    openai_api_key: str | None = "dummy_key"
    anthropic_base_url: str = "http://localhost:4141"
    anthropic_api_key: str | None = "dummy_key"
    proxy_base_url: str = "http://localhost:4141/v1"
    proxy_api_key: str = "dummy_key"
    proxy_model: str = "claude-sonnet-4.6"


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
    device: DeviceConfig = Field(default_factory=DeviceConfig)
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
