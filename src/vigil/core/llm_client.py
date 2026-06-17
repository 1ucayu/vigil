"""Unified LLM client wrapper (Google Gemini / Anthropic / Proxy).

Used ONLY during offline stages (state abstraction, DSL generation, Tier 3 evolution).
The online symbolic verifier must NEVER call this client for Tier 1-2.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from loguru import logger
from PIL import Image
from pydantic import BaseModel, ValidationError

from vigil.core.config import LLMConfig
from vigil.core.structured import (
    SchemaConstraintMode,
    StructuredResult,
    schema_hash,
    to_strict_schema,
)

load_dotenv()

_MAX_IMAGE_EDGE = 1280
_JPEG_QUALITY = 85
_MAX_RETRIES = 3
_BACKOFF_BASE = 1  # seconds

# Anthropic's Messages API REQUIRES max_tokens (no default; omitting it raises). This is the
# only output cap in this module and exists purely to satisfy the SDK contract, not to limit
# generation. It is set to a large model-output ceiling, never a cost/token budget.
_ANTHROPIC_MAX_OUTPUT_TOKENS = 64000
_ANTHROPIC_NON_STREAMING_MAX_OUTPUT_TOKENS = 16000
_PROXY_POST_READ_TIMEOUT_SECONDS = 90

# Substrings that mark a provider/proxy rejecting the JSON-Schema structured-output request
# (as opposed to an unrelated 400). Matched case-insensitively against the error text.
_SCHEMA_UNSUPPORTED_MARKERS = (
    "json_schema",
    "response_format",
    "response_schema",
    "additionalproperties",
    "not supported",
    "unsupported",
    "invalid schema",
    "strict",
)


class _StructuredProbeResponse(BaseModel):
    value: Literal["ok"]


@dataclass(frozen=True)
class _ProxyStructuredStrategy:
    name: str
    transport: str
    constraint_mode: SchemaConstraintMode


@dataclass(frozen=True)
class _ProxyProbeStatus:
    ok: bool
    detail: str


class _ProxyStructuredUnavailableError(Exception):
    """Raised when a proxy structured-output strategy cannot be used."""


def _disable_anthropic_thinking(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep Anthropic Messages calls lean and avoid hidden extended-thinking tails."""
    payload["thinking"] = {"type": "disabled"}
    return payload


def _extract_json(text: str) -> str:
    """Best-effort extraction of a single JSON object from model text (fallback path only)."""
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = [ln for ln in stripped.splitlines() if not ln.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


class LlmClient:
    """Unified LLM client for offline stages. Supports text and vision.

    Providers:
        - google: Google Gemini via google-genai SDK
        - anthropic: Anthropic Claude via anthropic SDK
        - openai: OpenAI-compatible chat completions API
        - proxy: OpenAI-compatible local proxy
    """

    _ENV_KEYS: dict[str, str] = {
        "google": "GOOGLE_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    _BASE_URL_ENV_KEYS: dict[str, str] = {
        "anthropic": "ANTHROPIC_BASE_URL",
        "openai": "OPENAI_BASE_URL",
    }

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._provider = config.provider

        if self._provider == "proxy":
            import openai

            self._client = openai.OpenAI(
                base_url=config.proxy_base_url,
                api_key=config.proxy_api_key,
            )
            self._model = config.proxy_model
            self._proxy_model_metadata_cache: dict[str, dict[str, Any]] = {}
            self._proxy_probe_cache: dict[tuple[str, str], _ProxyProbeStatus] = {}
        else:
            env_key = self._ENV_KEYS.get(self._provider, "")
            configured_api_key = {
                "anthropic": config.anthropic_api_key,
                "openai": config.openai_api_key,
            }.get(self._provider)
            api_key = os.environ.get(env_key) or configured_api_key or ""
            if not api_key:
                msg = f"Missing API key: set {env_key} environment variable"
                raise ValueError(msg)

            if self._provider == "google":
                from google import genai

                self._client = genai.Client(api_key=api_key)
                self._model = config.model or "gemini-2.5-pro"
            elif self._provider == "anthropic":
                import anthropic

                self._client = anthropic.Anthropic(
                    api_key=api_key,
                    base_url=self._base_url_for("anthropic", config.anthropic_base_url),
                )
                self._model = config.model or "claude-sonnet-4.6"
            elif self._provider == "openai":
                import openai

                self._client = openai.OpenAI(
                    base_url=self._base_url_for("openai", config.openai_base_url),
                    api_key=api_key,
                )
                self._model = config.model or "gpt-4.1"
            else:
                msg = f"Provider '{self._provider}' not yet implemented"
                raise NotImplementedError(msg)

    def _add_sampling_params(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Attach optional sampling parameters only when explicitly configured."""
        if self._config.temperature is not None:
            payload["temperature"] = self._config.temperature
        return payload

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Text-only generation."""
        if self._provider == "google":
            return self._generate_google(system_prompt, user_prompt, contents=[user_prompt])
        if self._provider == "proxy":
            return self._generate_proxy(system_prompt, user_prompt, images=None, image_labels=None)
        if self._provider == "openai":
            return self._generate_openai_compatible(system_prompt, user_prompt)
        return self._generate_anthropic(system_prompt, user_prompt)

    def generate_with_images(
        self,
        system_prompt: str,
        text_prompt: str,
        images: list[Path],
        image_labels: list[str] | None = None,
    ) -> str:
        """Multimodal generation with one or more images.

        Each image is loaded, resized to max 1280px longest edge,
        and sent as image content blocks.
        """
        if self._provider == "proxy":
            return self._generate_proxy(
                system_prompt,
                text_prompt,
                images=images,
                image_labels=image_labels,
            )
        if self._provider == "openai":
            return self._generate_openai_compatible_with_images(
                system_prompt, text_prompt, images, image_labels
            )

        labels = image_labels or [None] * len(images)
        pil_images = [self._preprocess_image(p) for p in images]

        if self._provider == "google":
            contents = self._google_contents(text_prompt, pil_images, labels)
            return self._generate_google(system_prompt, text_prompt, contents=contents)

        return self._generate_anthropic_with_images(system_prompt, text_prompt, pil_images, labels)

    @staticmethod
    def _google_contents(
        text_prompt: str,
        pil_images: list[Image.Image],
        labels: list[str | None],
    ) -> list[Any]:
        """Build the google-genai ``contents`` list (interleaved labels, PIL images, text)."""
        contents: list[Any] = []
        for label, img in zip(labels, pil_images, strict=True):
            if label:
                contents.append(label)
            contents.append(img)
        contents.append(text_prompt)
        return contents

    # ------------------------------------------------------------------
    # Structured (schema-constrained) generation
    # ------------------------------------------------------------------

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        *,
        allow_provider_fallback: bool = False,
    ) -> StructuredResult:
        """Generate a single object validated against ``response_model``.

        Prefers the provider's native structured-output mechanism. When that is unavailable
        and ``allow_provider_fallback`` is False, returns ``parsed=None`` with
        ``schema_constraint_mode="prompt_only_unavailable"`` — it never silently degrades to
        embedding the schema in a prompt. Only with ``allow_provider_fallback=True`` may the
        explicitly-recorded ``fallback_validate`` mode run.
        """
        return self._structured(
            system_prompt,
            user_prompt,
            response_model,
            schema_name,
            allow_provider_fallback,
            images=None,
            image_labels=None,
        )

    def generate_structured_with_images(
        self,
        system_prompt: str,
        text_prompt: str,
        images: list[Path],
        response_model: type[BaseModel],
        schema_name: str,
        image_labels: list[str] | None = None,
        *,
        allow_provider_fallback: bool = False,
    ) -> StructuredResult:
        """Multimodal :meth:`generate_structured`. Same constraint-mode semantics."""
        return self._structured(
            system_prompt,
            text_prompt,
            response_model,
            schema_name,
            allow_provider_fallback,
            images=images,
            image_labels=image_labels,
        )

    def probe_structured_output(
        self,
        response_model: type[BaseModel] = _StructuredProbeResponse,
        schema_name: str = "StructuredProbe",
        *,
        allow_provider_fallback: bool = False,
    ) -> StructuredResult:
        """Check whether the configured provider/model can enforce a strict schema."""
        user_prompt = (
            'Return {"value":"bad"}. Do not return ok.'
            if response_model is _StructuredProbeResponse
            else "Return the smallest valid object for the requested schema."
        )
        return self.generate_structured(
            "You are checking whether the provider enforces structured output.",
            user_prompt,
            response_model,
            schema_name,
            allow_provider_fallback=allow_provider_fallback,
        )

    def _structured(
        self,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        allow_provider_fallback: bool,
        *,
        images: list[Path] | None,
        image_labels: list[str] | None,
    ) -> StructuredResult:
        if self._provider == "proxy":
            return self._structured_proxy(
                system_prompt,
                text_prompt,
                response_model,
                schema_name,
                allow_provider_fallback,
                images,
                image_labels,
            )
        if self._provider == "openai":
            return self._structured_openai(
                system_prompt,
                text_prompt,
                response_model,
                schema_name,
                allow_provider_fallback,
                images,
                image_labels,
            )
        if self._provider == "google":
            return self._structured_google(
                system_prompt,
                text_prompt,
                response_model,
                schema_name,
                allow_provider_fallback,
                images,
                image_labels,
            )
        return self._structured_anthropic(
            system_prompt,
            text_prompt,
            response_model,
            schema_name,
            allow_provider_fallback,
            images,
            image_labels,
        )

    def _generate_proxy(
        self,
        system_prompt: str,
        text_prompt: str,
        *,
        images: list[Path] | None,
        image_labels: list[str] | None,
    ) -> str:
        metadata = self._proxy_model_metadata(self._model)
        vendor = self._proxy_vendor(metadata).lower()
        endpoints = set(metadata.get("supported_endpoints") or [])
        no_endpoint_metadata = not endpoints

        def has(endpoint: str) -> bool:
            return no_endpoint_metadata or endpoint in endpoints

        is_anthropic = "anthropic" in vendor or self._model.startswith("claude")
        if is_anthropic and has("/v1/messages"):
            return self._generate_proxy_anthropic_messages(
                system_prompt,
                text_prompt,
                images,
                image_labels,
                metadata,
            )
        if has("/chat/completions"):
            if images:
                return self._generate_openai_compatible_with_images(
                    system_prompt,
                    text_prompt,
                    images,
                    image_labels,
                )
            return self._generate_openai_compatible(system_prompt, text_prompt)
        if has("/responses"):
            return self._generate_proxy_responses(system_prompt, text_prompt, images, image_labels)
        raise ValueError(f"Proxy model {self._model!r} has no supported plain generation endpoint")

    def _generate_proxy_anthropic_messages(
        self,
        system_prompt: str,
        text_prompt: str,
        images: list[Path] | None,
        image_labels: list[str] | None,
        metadata: dict[str, Any],
    ) -> str:
        if images:
            labels = image_labels or [None] * len(images)
            pil_images = [self._preprocess_image(p) for p in images]
            content: Any = self._anthropic_image_content(text_prompt, pil_images, labels)
        else:
            content = text_prompt
        payload = {
            "model": self._model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
            # Anthropic Messages requires this field; use the model/proxy output ceiling,
            # not a project budget or artificial truncation limit.
            "max_tokens": self._proxy_anthropic_max_tokens(metadata),
        }
        _disable_anthropic_thinking(payload)
        self._add_sampling_params(payload)
        response = self._proxy_post_json("/messages", payload)
        return self._proxy_messages_text(response)

    def _generate_proxy_responses(
        self,
        system_prompt: str,
        text_prompt: str,
        images: list[Path] | None,
        image_labels: list[str] | None,
    ) -> str:
        user_content: Any = (
            self._openai_response_input_content(text_prompt, images, image_labels)
            if images
            else text_prompt
        )
        payload = {
            "model": self._model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        self._add_sampling_params(payload)
        response = self._proxy_post_json("/responses", payload)
        return self._proxy_response_output_text(response)

    def _structured_proxy(
        self,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        allow_provider_fallback: bool,
        images: list[Path] | None,
        image_labels: list[str] | None,
    ) -> StructuredResult:
        metadata = self._proxy_model_metadata(self._model)
        failures: list[str] = []
        for strategy in self._proxy_strategy_order(metadata):
            probe = self._proxy_probe_strategy(strategy, metadata)
            if not probe.ok:
                failures.append(f"{strategy.name}: {probe.detail}")
                continue
            try:
                result = self._run_proxy_strategy(
                    strategy,
                    system_prompt,
                    text_prompt,
                    response_model,
                    schema_name,
                    images,
                    image_labels,
                    metadata,
                    probe_status="ok",
                )
            except _ProxyStructuredUnavailableError as exc:
                failures.append(f"{strategy.name}: {exc}")
                continue
            if (
                allow_provider_fallback
                and result.parsed is None
                and result.schema_constraint_mode == "native_schema_unenforced"
            ):
                return self._fallback_validate(
                    system_prompt,
                    text_prompt,
                    response_model,
                    schema_name,
                    images,
                    image_labels,
                )
            return result

        reason = "; ".join(failures) or f"no structured strategy available for {self._model}"
        if allow_provider_fallback:
            return self._fallback_validate(
                system_prompt,
                text_prompt,
                response_model,
                schema_name,
                images,
                image_labels,
            )
        logger.warning(f"Structured output unavailable ({reason}); failing clearly.")
        return StructuredResult(
            parsed=None,
            raw_text="",
            provider=self._provider,
            model=self._model,
            schema_name=schema_name,
            schema_hash=schema_hash(response_model),
            schema_constraint_mode="prompt_only_unavailable",
            validation_errors=[reason],
            strategy="unavailable",
            vendor=self._proxy_vendor(metadata),
            probe_status="failed",
        )

    def _proxy_model_metadata(self, model: str) -> dict[str, Any]:
        cache = getattr(self, "_proxy_model_metadata_cache", None)
        if cache is None:
            return {}
        if model in cache:
            return cache[model]

        try:
            payload = self._proxy_get_json(self._config.proxy_models_url)
        except Exception as exc:  # pragma: no cover - depends on optional local proxy.
            logger.warning(f"Could not query proxy model metadata: {exc}")
            cache[model] = {}
            return {}

        data = payload.get("data", []) if isinstance(payload, dict) else []
        for item in data:
            if not isinstance(item, dict):
                continue
            ids = {str(item.get("id") or ""), str(item.get("claude_model_id") or "")}
            if model in ids:
                cache[model] = item
                return item
        cache[model] = {}
        return {}

    def _proxy_probe_strategy(
        self,
        strategy: _ProxyStructuredStrategy,
        metadata: dict[str, Any],
    ) -> _ProxyProbeStatus:
        cache = getattr(self, "_proxy_probe_cache", None)
        cache_key = (self._model, strategy.name)
        if cache is not None and cache_key in cache:
            return cache[cache_key]

        try:
            result = self._run_proxy_strategy(
                strategy,
                "You are checking whether the provider enforces structured output.",
                'Return {"value":"bad"}. Do not return ok.',
                _StructuredProbeResponse,
                "StructuredProbe",
                images=None,
                image_labels=None,
                metadata=metadata,
                probe_status="probe",
            )
        except Exception as exc:
            status = _ProxyProbeStatus(False, str(exc))
        else:
            parsed = result.parsed
            if isinstance(parsed, _StructuredProbeResponse) and parsed.value == "ok":
                status = _ProxyProbeStatus(True, "ok")
            else:
                detail = (
                    result.validation_errors[0] if result.validation_errors else result.raw_text
                )
                status = _ProxyProbeStatus(False, detail or "schema was not enforced")
        if cache is not None:
            cache[cache_key] = status
        return status

    def _proxy_strategy_order(self, metadata: dict[str, Any]) -> list[_ProxyStructuredStrategy]:
        vendor = self._proxy_vendor(metadata).lower()
        endpoints = set(metadata.get("supported_endpoints") or [])
        supports = (metadata.get("capabilities") or {}).get("supports") or {}
        no_endpoint_metadata = not endpoints

        def has_endpoint(endpoint: str) -> bool:
            return no_endpoint_metadata or endpoint in endpoints

        def supports_flag(flag: str) -> bool:
            return no_endpoint_metadata or bool(supports.get(flag))

        is_anthropic = "anthropic" in vendor or self._model.startswith("claude")
        is_google = "google" in vendor or self._model.startswith("gemini")
        is_openai = (
            "openai" in vendor
            or "azure" in vendor
            or self._model.startswith("gpt")
            or self._model.startswith("mai-")
        )

        strategies: list[_ProxyStructuredStrategy] = []

        def add(name: str, transport: str, mode: SchemaConstraintMode) -> None:
            strategy = _ProxyStructuredStrategy(name, transport, mode)
            if strategy not in strategies:
                strategies.append(strategy)

        if is_anthropic:
            if has_endpoint("/v1/messages") and supports_flag("tool_calls"):
                add("anthropic_messages_tool", "/v1/messages", "tool_schema")
            if has_endpoint("/chat/completions") and supports_flag("tool_calls"):
                add("chat_function_tool", "/chat/completions", "tool_schema")
        elif is_google:
            if has_endpoint("/chat/completions") and supports_flag("tool_calls"):
                add("chat_function_tool", "/chat/completions", "tool_schema")
            if has_endpoint("/chat/completions"):
                add("chat_json_schema", "/chat/completions", "native_schema")
        elif is_openai:
            if has_endpoint("/responses") and supports_flag("structured_outputs"):
                add("responses_json_schema", "/responses", "native_schema")
            if has_endpoint("/chat/completions") and supports_flag("structured_outputs"):
                add("chat_json_schema", "/chat/completions", "native_schema")
            if has_endpoint("/chat/completions") and supports_flag("tool_calls"):
                add("chat_function_tool", "/chat/completions", "tool_schema")
        else:
            if has_endpoint("/v1/messages") and supports_flag("tool_calls"):
                add("anthropic_messages_tool", "/v1/messages", "tool_schema")
            if has_endpoint("/responses") and bool(supports.get("structured_outputs")):
                add("responses_json_schema", "/responses", "native_schema")
            if has_endpoint("/chat/completions") and supports_flag("tool_calls"):
                add("chat_function_tool", "/chat/completions", "tool_schema")
            if has_endpoint("/chat/completions") and bool(supports.get("structured_outputs")):
                add("chat_json_schema", "/chat/completions", "native_schema")

        return strategies

    def _run_proxy_strategy(
        self,
        strategy: _ProxyStructuredStrategy,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        images: list[Path] | None,
        image_labels: list[str] | None,
        metadata: dict[str, Any],
        *,
        probe_status: str,
    ) -> StructuredResult:
        if strategy.name == "anthropic_messages_tool":
            return self._proxy_anthropic_messages_tool(
                system_prompt,
                text_prompt,
                response_model,
                schema_name,
                images,
                image_labels,
                metadata,
                strategy,
                probe_status,
            )
        if strategy.name == "chat_function_tool":
            return self._proxy_chat_function_tool(
                system_prompt,
                text_prompt,
                response_model,
                schema_name,
                images,
                image_labels,
                metadata,
                strategy,
                probe_status,
            )
        if strategy.name == "responses_json_schema":
            return self._proxy_responses_json_schema(
                system_prompt,
                text_prompt,
                response_model,
                schema_name,
                images,
                image_labels,
                metadata,
                strategy,
                probe_status,
            )
        if strategy.name == "chat_json_schema":
            return self._proxy_chat_json_schema(
                system_prompt,
                text_prompt,
                response_model,
                schema_name,
                images,
                image_labels,
                metadata,
                strategy,
                probe_status,
            )
        raise _ProxyStructuredUnavailableError(f"unknown strategy {strategy.name}")

    def _proxy_anthropic_messages_tool(
        self,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        images: list[Path] | None,
        image_labels: list[str] | None,
        metadata: dict[str, Any],
        strategy: _ProxyStructuredStrategy,
        probe_status: str,
    ) -> StructuredResult:
        content: Any
        if images:
            labels = image_labels or [None] * len(images)
            pil_images = [self._preprocess_image(p) for p in images]
            content = self._anthropic_image_content(text_prompt, pil_images, labels)
        else:
            content = text_prompt

        tool = {
            "name": schema_name,
            "description": f"Return exactly one well-formed {schema_name} object.",
            "input_schema": to_strict_schema(response_model, inline_refs=True),
            "strict": True,
        }
        payload = {
            "model": self._model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": schema_name},
            "max_tokens": self._proxy_anthropic_max_tokens(metadata),
        }
        _disable_anthropic_thinking(payload)
        self._add_sampling_params(payload)
        response = self._proxy_post_json("/messages", payload)
        block = next(
            (
                b
                for b in response.get("content", [])
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ),
            None,
        )
        raw = json.dumps(block.get("input")) if isinstance(block, dict) else ""
        parsed, errors = self._validate_proxy_obj(
            response_model,
            block.get("input") if block else None,
        )
        stop = str(response.get("stop_reason") or "")
        return self._proxy_structured_result(
            parsed,
            raw,
            response_model,
            schema_name,
            strategy,
            metadata,
            probe_status,
            refusal="refused" if stop == "refusal" else None,
            stop_reason=stop or None,
            incomplete=(stop == "max_tokens"),
            incomplete_detail="max_tokens" if stop == "max_tokens" else None,
            validation_errors=errors or ([] if block else ["forced tool call was not returned"]),
        )

    def _proxy_chat_function_tool(
        self,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        images: list[Path] | None,
        image_labels: list[str] | None,
        metadata: dict[str, Any],
        strategy: _ProxyStructuredStrategy,
        probe_status: str,
    ) -> StructuredResult:
        user_content: Any = (
            self._openai_image_content(text_prompt, images, image_labels) if images else text_prompt
        )
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": schema_name,
                        "description": f"Return exactly one well-formed {schema_name} object.",
                        "parameters": to_strict_schema(response_model, inline_refs=True),
                        "strict": True,
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": schema_name}},
        }
        self._add_sampling_params(payload)
        response = self._proxy_post_json("/chat/completions", payload)
        choice = self._proxy_first_choice(response)
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        raw = self._proxy_tool_arguments(message)
        parsed, errors = self._validate_proxy_json(response_model, raw)
        finish = str(choice.get("finish_reason") or "") if isinstance(choice, dict) else ""
        return self._proxy_structured_result(
            parsed,
            raw,
            response_model,
            schema_name,
            strategy,
            metadata,
            probe_status,
            refusal=message.get("refusal") if isinstance(message, dict) else None,
            stop_reason=finish or None,
            incomplete=(finish == "length"),
            incomplete_detail="length" if finish == "length" else None,
            validation_errors=errors or ([] if raw else ["forced tool call was not returned"]),
        )

    def _proxy_responses_json_schema(
        self,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        images: list[Path] | None,
        image_labels: list[str] | None,
        metadata: dict[str, Any],
        strategy: _ProxyStructuredStrategy,
        probe_status: str,
    ) -> StructuredResult:
        user_content: Any = (
            self._openai_response_input_content(text_prompt, images, image_labels)
            if images
            else text_prompt
        )
        payload = {
            "model": self._model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": to_strict_schema(response_model, inline_refs=True),
                }
            },
        }
        self._add_sampling_params(payload)
        response = self._proxy_post_json("/responses", payload)
        raw = self._proxy_response_output_text(response)
        parsed, errors = self._validate_proxy_json(response_model, raw)
        mode = "native_schema" if parsed is not None else "native_schema_unenforced"
        status = str(response.get("status") or "")
        return self._proxy_structured_result(
            parsed,
            raw,
            response_model,
            schema_name,
            strategy,
            metadata,
            probe_status,
            mode=mode,
            refusal=self._proxy_response_refusal(response),
            stop_reason=status or None,
            incomplete=(status == "incomplete"),
            incomplete_detail=str(response.get("incomplete_details") or "") or None,
            validation_errors=errors,
        )

    def _proxy_chat_json_schema(
        self,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        images: list[Path] | None,
        image_labels: list[str] | None,
        metadata: dict[str, Any],
        strategy: _ProxyStructuredStrategy,
        probe_status: str,
    ) -> StructuredResult:
        user_content: Any = (
            self._openai_image_content(text_prompt, images, image_labels) if images else text_prompt
        )
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": to_strict_schema(response_model, inline_refs=True),
                },
            },
        }
        self._add_sampling_params(payload)
        response = self._proxy_post_json("/chat/completions", payload)
        choice = self._proxy_first_choice(response)
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        raw = str(message.get("content") or "") if isinstance(message, dict) else ""
        parsed, errors = self._validate_proxy_json(response_model, raw)
        mode = "native_schema" if parsed is not None else "native_schema_unenforced"
        finish = str(choice.get("finish_reason") or "") if isinstance(choice, dict) else ""
        return self._proxy_structured_result(
            parsed,
            raw,
            response_model,
            schema_name,
            strategy,
            metadata,
            probe_status,
            mode=mode,
            refusal=message.get("refusal") if isinstance(message, dict) else None,
            stop_reason=finish or None,
            incomplete=(finish == "length"),
            incomplete_detail="length" if finish == "length" else None,
            validation_errors=errors,
        )

    def _proxy_structured_result(
        self,
        parsed: BaseModel | None,
        raw: str,
        response_model: type[BaseModel],
        schema_name: str,
        strategy: _ProxyStructuredStrategy,
        metadata: dict[str, Any],
        probe_status: str,
        *,
        mode: SchemaConstraintMode | None = None,
        refusal: str | None = None,
        stop_reason: str | None = None,
        incomplete: bool = False,
        incomplete_detail: str | None = None,
        validation_errors: list[str] | None = None,
    ) -> StructuredResult:
        return StructuredResult(
            parsed=parsed,
            raw_text=raw,
            provider=self._provider,
            model=self._model,
            schema_name=schema_name,
            schema_hash=schema_hash(response_model),
            schema_constraint_mode=mode or strategy.constraint_mode,
            refusal=refusal,
            stop_reason=stop_reason,
            incomplete=incomplete,
            incomplete_detail=incomplete_detail,
            validation_errors=validation_errors or [],
            transport=strategy.transport,
            strategy=strategy.name,
            vendor=self._proxy_vendor(metadata),
            probe_status=probe_status,
        )

    def _proxy_get_json(self, url: str) -> dict[str, Any]:
        import httpx

        response = httpx.get(url, headers=self._proxy_headers(), timeout=10)
        if response.status_code >= 400:
            raise _ProxyStructuredUnavailableError(f"HTTP {response.status_code}: {response.text}")
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _proxy_post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        url = self._proxy_url(path)

        def _call() -> dict[str, Any]:
            timeout = httpx.Timeout(
                connect=10,
                read=_PROXY_POST_READ_TIMEOUT_SECONDS,
                write=30,
                pool=10,
            )
            response = httpx.post(url, headers=self._proxy_headers(), json=payload, timeout=timeout)
            if response.status_code >= 400:
                detail = response.text
                if self._is_schema_unsupported(Exception(detail)):
                    raise _ProxyStructuredUnavailableError(
                        f"{path} schema request unsupported: {detail}"
                    )
                raise _ProxyStructuredUnavailableError(
                    f"{path} HTTP {response.status_code}: {detail}"
                )
            parsed = response.json()
            return parsed if isinstance(parsed, dict) else {}

        return self._call_with_retry(_call)

    def _proxy_url(self, path: str) -> str:
        return f"{self._config.proxy_base_url.rstrip('/')}/{path.lstrip('/')}"

    def _proxy_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.proxy_api_key}",
            "Content-Type": "application/json",
        }

    def _proxy_vendor(self, metadata: dict[str, Any]) -> str:
        vendor = str(metadata.get("vendor") or metadata.get("owned_by") or "")
        if vendor:
            return vendor
        if self._model.startswith("claude"):
            return "Anthropic"
        if self._model.startswith("gemini"):
            return "Google"
        if self._model.startswith("gpt") or self._model.startswith("mai-"):
            return "OpenAI"
        return "unknown"

    @staticmethod
    def _proxy_first_choice(response: dict[str, Any]) -> dict[str, Any]:
        choices = response.get("choices") if isinstance(response, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            return choices[0]
        return {}

    @staticmethod
    def _proxy_tool_arguments(message: dict[str, Any]) -> str:
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
        if not isinstance(tool_calls, list) or not tool_calls:
            return ""
        call = tool_calls[0]
        if not isinstance(call, dict):
            return ""
        function = call.get("function")
        if not isinstance(function, dict):
            return ""
        args = function.get("arguments")
        if isinstance(args, str):
            return args
        if isinstance(args, dict):
            return json.dumps(args)
        return ""

    @staticmethod
    def _proxy_messages_text(response: dict[str, Any]) -> str:
        chunks: list[str] = []
        content = response.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str):
                chunks.append(text)
        return "".join(chunks)

    @staticmethod
    def _proxy_response_output_text(response: dict[str, Any]) -> str:
        output_text = response.get("output_text")
        if isinstance(output_text, str):
            return output_text
        chunks: list[str] = []
        for item in response.get("output", []) if isinstance(response.get("output"), list) else []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks)

    @staticmethod
    def _proxy_response_refusal(response: dict[str, Any]) -> str | None:
        for item in response.get("output", []) if isinstance(response.get("output"), list) else []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
                if isinstance(content, dict) and content.get("type") == "refusal":
                    refusal = content.get("refusal") or content.get("text")
                    return str(refusal) if refusal else "refusal"
        return None

    @staticmethod
    def _validate_proxy_json(
        response_model: type[BaseModel],
        raw: str,
    ) -> tuple[BaseModel | None, list[str]]:
        if not raw:
            return None, ["structured response was empty"]
        try:
            return response_model.model_validate_json(raw), []
        except ValidationError as exc:
            return None, [str(exc)]

    @staticmethod
    def _validate_proxy_obj(
        response_model: type[BaseModel],
        obj: Any,
    ) -> tuple[BaseModel | None, list[str]]:
        if obj is None:
            return None, ["structured response was empty"]
        try:
            return response_model.model_validate(obj), []
        except ValidationError as exc:
            return None, [str(exc)]

    @staticmethod
    def _proxy_anthropic_max_tokens(metadata: dict[str, Any]) -> int:
        limits = (metadata.get("capabilities") or {}).get("limits") or {}
        value = limits.get("max_non_streaming_output_tokens") or limits.get("max_output_tokens")
        if isinstance(value, int) and value > 0:
            return min(value, _ANTHROPIC_NON_STREAMING_MAX_OUTPUT_TOKENS)
        return _ANTHROPIC_NON_STREAMING_MAX_OUTPUT_TOKENS

    def _openai_response_input_content(
        self,
        text_prompt: str,
        images: list[Path],
        image_labels: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        labels = image_labels or [None] * len(images)
        pil_images = [self._preprocess_image(p) for p in images]
        content: list[dict[str, Any]] = []
        for label, img in zip(labels, pil_images, strict=True):
            if label:
                content.append({"type": "input_text", "text": label})
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
            b64 = base64.b64encode(buf.getvalue()).decode()
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{b64}",
                }
            )
        content.append({"type": "input_text", "text": text_prompt})
        return content

    def _structured_openai(
        self,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        allow_provider_fallback: bool,
        images: list[Path] | None,
        image_labels: list[str] | None,
    ) -> StructuredResult:
        import openai

        length_err = getattr(openai, "LengthFinishReasonError", ())
        content_filter_err = getattr(openai, "ContentFilterFinishReasonError", ())
        user_content: Any = (
            self._openai_image_content(text_prompt, images, image_labels) if images else text_prompt
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        if not hasattr(self._client.chat.completions, "parse"):
            return self._unavailable_or_fallback(
                system_prompt,
                text_prompt,
                response_model,
                schema_name,
                allow_provider_fallback,
                images,
                image_labels,
                reason="openai SDK lacks chat.completions.parse",
            )

        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "response_format": response_model,
            }
            self._add_sampling_params(kwargs)
            return self._client.chat.completions.parse(**kwargs)

        try:
            completion = self._call_with_retry(_call)
        except openai.BadRequestError as exc:
            if self._is_schema_unsupported(exc):
                return self._unavailable_or_fallback(
                    system_prompt,
                    text_prompt,
                    response_model,
                    schema_name,
                    allow_provider_fallback,
                    images,
                    image_labels,
                    reason=str(exc),
                )
            raise
        except length_err:
            return self._native_failure(
                response_model,
                schema_name,
                "native_schema",
                stop_reason="length",
                incomplete=True,
                incomplete_detail="length",
            )
        except content_filter_err:
            return self._native_failure(
                response_model,
                schema_name,
                "native_schema",
                refusal="content_filter",
                stop_reason="content_filter",
                incomplete=True,
                incomplete_detail="content_filter",
            )

        choice = completion.choices[0]
        message = choice.message
        finish = getattr(choice, "finish_reason", None)
        raw = getattr(message, "content", None) or ""
        parsed = getattr(message, "parsed", None)
        errors: list[str] = []
        mode: SchemaConstraintMode = "native_schema"
        if parsed is None and raw:
            try:
                parsed = response_model.model_validate_json(raw)
            except ValidationError as exc:
                errors.append(str(exc))
                mode = "native_schema_unenforced"
        return StructuredResult(
            parsed=parsed,
            raw_text=raw,
            provider=self._provider,
            model=self._model,
            schema_name=schema_name,
            schema_hash=schema_hash(response_model),
            schema_constraint_mode=mode,
            refusal=getattr(message, "refusal", None),
            stop_reason=finish,
            incomplete=(finish == "length"),
            incomplete_detail="length" if finish == "length" else None,
            validation_errors=errors,
            transport="/chat/completions",
            strategy="openai_chat_parse",
            vendor="OpenAI",
            probe_status="provider_native",
        )

    def _structured_google(
        self,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        allow_provider_fallback: bool,
        images: list[Path] | None,
        image_labels: list[str] | None,
    ) -> StructuredResult:
        from google.genai import types

        if images:
            labels = image_labels or [None] * len(images)
            pil_images = [self._preprocess_image(p) for p in images]
            contents = self._google_contents(text_prompt, pil_images, labels)
        else:
            contents = [text_prompt]

        config_kwargs: dict[str, Any] = {
            "system_instruction": system_prompt,
            "response_mime_type": "application/json",
            "response_schema": response_model,
        }
        self._add_sampling_params(config_kwargs)
        config = types.GenerateContentConfig(**config_kwargs)

        def _call() -> Any:
            return self._client.models.generate_content(
                model=self._model, contents=contents, config=config
            )

        response = self._call_with_retry(_call)
        raw = response.text or ""
        parsed = getattr(response, "parsed", None)
        errors: list[str] = []
        if parsed is None and raw:
            try:
                parsed = response_model.model_validate_json(raw)
            except ValidationError as exc:
                errors.append(str(exc))
        mode: SchemaConstraintMode = (
            "native_schema" if parsed is not None else "native_schema_unenforced"
        )
        candidate = (getattr(response, "candidates", None) or [None])[0]
        finish = getattr(getattr(candidate, "finish_reason", None), "value", None)
        blocked = finish in {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
        return StructuredResult(
            parsed=parsed,
            raw_text=raw,
            provider=self._provider,
            model=self._model,
            schema_name=schema_name,
            schema_hash=schema_hash(response_model),
            schema_constraint_mode=mode,
            refusal=finish if blocked else None,
            stop_reason=finish,
            incomplete=(finish == "MAX_TOKENS"),
            incomplete_detail="MAX_TOKENS" if finish == "MAX_TOKENS" else None,
            validation_errors=errors,
            transport="google-genai",
            strategy="google_response_schema",
            vendor="Google",
            probe_status="provider_native",
        )

    def _structured_anthropic(
        self,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        allow_provider_fallback: bool,
        images: list[Path] | None,
        image_labels: list[str] | None,
    ) -> StructuredResult:
        if images:
            labels = image_labels or [None] * len(images)
            pil_images = [self._preprocess_image(p) for p in images]
            content: Any = self._anthropic_image_content(text_prompt, pil_images, labels)
        else:
            content = text_prompt

        tool = {
            "name": schema_name,
            "description": f"Return exactly one well-formed {schema_name} object.",
            "input_schema": to_strict_schema(response_model),
            "strict": True,
        }

        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": content}],
                "tools": [tool],
                "tool_choice": {"type": "tool", "name": schema_name},
                "max_tokens": _ANTHROPIC_MAX_OUTPUT_TOKENS,
            }
            _disable_anthropic_thinking(kwargs)
            self._add_sampling_params(kwargs)
            return self._client.messages.create(**kwargs)

        message = self._call_with_retry(_call)
        block = next((b for b in message.content if getattr(b, "type", None) == "tool_use"), None)
        parsed: BaseModel | None = None
        raw = ""
        errors: list[str] = []
        if block is not None:
            raw = json.dumps(block.input)
            try:
                parsed = response_model.model_validate(block.input)
            except ValidationError as exc:
                errors.append(str(exc))
        stop = getattr(message, "stop_reason", None)
        return StructuredResult(
            parsed=parsed,
            raw_text=raw,
            provider=self._provider,
            model=self._model,
            schema_name=schema_name,
            schema_hash=schema_hash(response_model),
            schema_constraint_mode="tool_schema",
            refusal="refused" if stop == "refusal" else None,
            stop_reason=stop,
            incomplete=(stop == "max_tokens"),
            incomplete_detail="max_tokens" if stop == "max_tokens" else None,
            validation_errors=errors,
            transport="/v1/messages",
            strategy="anthropic_messages_tool",
            vendor="Anthropic",
            probe_status="provider_native",
        )

    def _native_failure(
        self,
        response_model: type[BaseModel],
        schema_name: str,
        mode: SchemaConstraintMode,
        *,
        refusal: str | None = None,
        stop_reason: str | None = None,
        incomplete: bool = False,
        incomplete_detail: str | None = None,
        validation_errors: list[str] | None = None,
    ) -> StructuredResult:
        return StructuredResult(
            parsed=None,
            raw_text="",
            provider=self._provider,
            model=self._model,
            schema_name=schema_name,
            schema_hash=schema_hash(response_model),
            schema_constraint_mode=mode,
            refusal=refusal,
            stop_reason=stop_reason,
            incomplete=incomplete,
            incomplete_detail=incomplete_detail,
            validation_errors=validation_errors or [],
            strategy="unavailable",
            vendor=self._provider,
            probe_status="failed",
        )

    def _unavailable_or_fallback(
        self,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        allow_provider_fallback: bool,
        images: list[Path] | None,
        image_labels: list[str] | None,
        *,
        reason: str,
    ) -> StructuredResult:
        """Native structured output is unavailable.

        Without opt-in, returns ``prompt_only_unavailable`` (never a prompt-only degrade).
        With ``allow_provider_fallback=True``, runs the explicitly-recorded
        ``fallback_validate`` mode.
        """
        if not allow_provider_fallback:
            logger.warning(f"Structured output unavailable ({reason}); failing clearly.")
            return self._native_failure(
                response_model,
                schema_name,
                "prompt_only_unavailable",
                validation_errors=[reason],
            )
        return self._fallback_validate(
            system_prompt, text_prompt, response_model, schema_name, images, image_labels
        )

    def _fallback_validate(
        self,
        system_prompt: str,
        text_prompt: str,
        response_model: type[BaseModel],
        schema_name: str,
        images: list[Path] | None,
        image_labels: list[str] | None,
    ) -> StructuredResult:
        schema_json = json.dumps(to_strict_schema(response_model), indent=2, sort_keys=True)
        augmented = (
            f"{text_prompt}\n\n"
            "Respond with ONLY a single JSON object that validates against this JSON Schema. "
            "No markdown fences, no commentary:\n"
            f"{schema_json}"
        )
        if images:
            raw = self.generate_with_images(system_prompt, augmented, images, image_labels)
        else:
            raw = self.generate(system_prompt, augmented)
        text = _extract_json(raw)
        parsed: BaseModel | None = None
        errors: list[str] = []
        try:
            parsed = response_model.model_validate_json(text)
        except ValidationError as exc:
            errors.append(str(exc))
        return StructuredResult(
            parsed=parsed,
            raw_text=raw,
            provider=self._provider,
            model=self._model,
            schema_name=schema_name,
            schema_hash=schema_hash(response_model),
            schema_constraint_mode="fallback_validate",
            validation_errors=errors,
            transport="prompt",
            strategy="fallback_validate",
            vendor=self._provider,
            probe_status="explicit_fallback",
        )

    @staticmethod
    def _is_schema_unsupported(exc: Exception) -> bool:
        text = str(getattr(exc, "message", "") or exc).lower()
        return any(marker in text for marker in _SCHEMA_UNSUPPORTED_MARKERS)

    def _generate_google(self, system_prompt: str, _text_prompt: str, contents: list[Any]) -> str:
        from google.genai import types

        config_kwargs: dict[str, Any] = {"systemInstruction": system_prompt}
        self._add_sampling_params(config_kwargs)
        config = types.GenerateContentConfig(**config_kwargs)

        def _call() -> Any:
            return self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )

        response = self._call_with_retry(_call)
        # Log token usage
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            um = response.usage_metadata
            logger.debug(
                f"Gemini tokens: input={getattr(um, 'prompt_token_count', '?')}, "
                f"output={getattr(um, 'candidates_token_count', '?')}"
            )
        return response.text or ""

    def _generate_anthropic(self, system_prompt: str, user_prompt: str) -> str:
        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
                "max_tokens": _ANTHROPIC_MAX_OUTPUT_TOKENS,
            }
            _disable_anthropic_thinking(kwargs)
            self._add_sampling_params(kwargs)
            return self._client.messages.create(**kwargs)

        response = self._call_with_retry(_call)
        if getattr(response, "usage", None):
            logger.debug(
                f"Anthropic tokens: input={response.usage.input_tokens}, "
                f"output={response.usage.output_tokens}"
            )
        return response.content[0].text

    def _generate_anthropic_with_images(
        self,
        system_prompt: str,
        text_prompt: str,
        images: list[Image.Image],
        labels: list[str | None],
    ) -> str:
        content = self._anthropic_image_content(text_prompt, images, labels)

        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": _ANTHROPIC_MAX_OUTPUT_TOKENS,
            }
            _disable_anthropic_thinking(kwargs)
            self._add_sampling_params(kwargs)
            return self._client.messages.create(**kwargs)

        response = self._call_with_retry(_call)
        if getattr(response, "usage", None):
            logger.debug(
                f"Anthropic tokens: input={response.usage.input_tokens}, "
                f"output={response.usage.output_tokens}"
            )
        return response.content[0].text

    def _anthropic_image_content(
        self,
        text_prompt: str,
        images: list[Image.Image],
        labels: list[str | None],
    ) -> list[dict[str, Any]]:
        """Build the Anthropic user content list (labels + base64 image blocks + text)."""
        content: list[dict[str, Any]] = []
        for label, img in zip(labels, images, strict=True):
            if label:
                content.append({"type": "text", "text": label})
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
            b64 = base64.b64encode(buf.getvalue()).decode()
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                }
            )
        content.append({"type": "text", "text": text_prompt})
        return content

    def _generate_openai_compatible(self, system_prompt: str, user_prompt: str) -> str:
        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            self._add_sampling_params(kwargs)
            return self._client.chat.completions.create(**kwargs)

        response = self._call_with_retry(_call)
        if hasattr(response, "usage") and response.usage:
            logger.debug(
                f"OpenAI-compatible tokens: input={response.usage.prompt_tokens}, "
                f"output={response.usage.completion_tokens}"
            )
        return self._openai_message_content(response)

    def _generate_openai_compatible_with_images(
        self,
        system_prompt: str,
        text_prompt: str,
        images: list[Path],
        image_labels: list[str] | None = None,
    ) -> str:
        """Multimodal generation via OpenAI-compatible proxy with base64 images."""
        content = self._openai_image_content(text_prompt, images, image_labels)

        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
            }
            self._add_sampling_params(kwargs)
            return self._client.chat.completions.create(**kwargs)

        response = self._call_with_retry(_call)
        if hasattr(response, "usage") and response.usage:
            logger.debug(
                f"OpenAI-compatible tokens: input={response.usage.prompt_tokens}, "
                f"output={response.usage.completion_tokens}"
            )
        return self._openai_message_content(response)

    def _openai_image_content(
        self,
        text_prompt: str,
        images: list[Path],
        image_labels: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the OpenAI-compatible user content list (labels + base64 image_url blocks)."""
        labels = image_labels or [None] * len(images)
        pil_images = [self._preprocess_image(p) for p in images]
        content: list[dict[str, Any]] = []
        for label, img in zip(labels, pil_images, strict=True):
            if label:
                content.append({"type": "text", "text": label})
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
            b64 = base64.b64encode(buf.getvalue()).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )
        content.append({"type": "text", "text": text_prompt})
        return content

    @staticmethod
    def _openai_message_content(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            raise ValueError("LLM response contained no choices")
        message = getattr(choices[0], "message", None)
        return getattr(message, "content", None) or ""

    def _base_url_for(self, provider: str, configured_base_url: str) -> str:
        env_key = self._BASE_URL_ENV_KEYS.get(provider, "")
        return os.environ.get(env_key) or configured_base_url

    @staticmethod
    def _preprocess_image(path: Path) -> Image.Image:
        """Load and resize image so longest edge ≤ 1280px."""
        img = Image.open(path)
        if img.mode == "RGBA":
            img = img.convert("RGB")
        w, h = img.size
        longest = max(w, h)
        if longest > _MAX_IMAGE_EDGE:
            scale = _MAX_IMAGE_EDGE / longest
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        return img

    def _call_with_retry(self, fn: Callable[[], Any]) -> Any:
        """Call fn with exponential backoff retry on transient errors."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                is_retryable = self._is_retryable(exc)
                if not is_retryable:
                    raise
                backoff = _BACKOFF_BASE * (2**attempt)
                logger.warning(
                    f"LLM call failed (attempt {attempt + 1}/{_MAX_RETRIES}), "
                    f"retrying in {backoff}s: {exc}"
                )
                time.sleep(backoff)
        raise last_exc  # type: ignore[misc]

    def _is_retryable(self, exc: Exception) -> bool:
        """Check if an exception is transient and should be retried."""
        try:
            import httpx
        except Exception:  # pragma: no cover - httpx is an optional transport dependency.
            httpx = None  # type: ignore[assignment]
        if httpx is not None and isinstance(exc, httpx.TimeoutException | httpx.NetworkError):
            return True

        exc_type = type(exc).__name__
        # Rate limit and transient errors
        retryable_names = {"RateLimitError", "APIConnectionError", "ClientError", "ServerError"}
        if exc_type in retryable_names:
            return True
        # Check for HTTP 429/500/502/503 in string repr
        exc_str = str(exc)
        return any(code in exc_str for code in ("429", "500", "502", "503"))
