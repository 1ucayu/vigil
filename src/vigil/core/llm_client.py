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
from pathlib import Path
from typing import Any

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

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Text-only generation."""
        if self._provider == "google":
            return self._generate_google(system_prompt, user_prompt, contents=[user_prompt])
        if self._provider in {"openai", "proxy"}:
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
        if self._provider in {"openai", "proxy"}:
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
        if self._provider in {"openai", "proxy"}:
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
            return self._client.chat.completions.parse(
                model=self._model,
                messages=messages,
                response_format=response_model,
                temperature=self._config.temperature,
            )

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
        return StructuredResult(
            parsed=getattr(message, "parsed", None),
            raw_text=getattr(message, "content", None) or "",
            provider=self._provider,
            model=self._model,
            schema_name=schema_name,
            schema_hash=schema_hash(response_model),
            schema_constraint_mode="native_schema",
            refusal=getattr(message, "refusal", None),
            stop_reason=finish,
            incomplete=(finish == "length"),
            incomplete_detail="length" if finish == "length" else None,
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

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=self._config.temperature,
            response_mime_type="application/json",
            response_schema=response_model,
        )

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
            schema_constraint_mode="native_schema",
            refusal=finish if blocked else None,
            stop_reason=finish,
            incomplete=(finish == "MAX_TOKENS"),
            incomplete_detail="MAX_TOKENS" if finish == "MAX_TOKENS" else None,
            validation_errors=errors,
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
        }

        def _call() -> Any:
            return self._client.messages.create(
                model=self._model,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
                temperature=self._config.temperature,
                tools=[tool],
                tool_choice={"type": "tool", "name": schema_name},
                max_tokens=_ANTHROPIC_MAX_OUTPUT_TOKENS,
            )

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
        )

    @staticmethod
    def _is_schema_unsupported(exc: Exception) -> bool:
        text = str(getattr(exc, "message", "") or exc).lower()
        return any(marker in text for marker in _SCHEMA_UNSUPPORTED_MARKERS)

    def _generate_google(self, system_prompt: str, _text_prompt: str, contents: list[Any]) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            systemInstruction=system_prompt,
            temperature=self._config.temperature,
        )

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
                "temperature": self._config.temperature,
            }
            return self._client.messages.create(**kwargs)

        response = self._call_with_retry(_call)
        if hasattr(response, "usage"):
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
                "temperature": self._config.temperature,
            }
            return self._client.messages.create(**kwargs)

        response = self._call_with_retry(_call)
        if hasattr(response, "usage"):
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
                "temperature": self._config.temperature,
            }
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
                "temperature": self._config.temperature,
            }
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
        exc_type = type(exc).__name__
        # Rate limit and transient errors
        retryable_names = {"RateLimitError", "APIConnectionError", "ClientError", "ServerError"}
        if exc_type in retryable_names:
            return True
        # Check for HTTP 429/500/502/503 in string repr
        exc_str = str(exc)
        return any(code in exc_str for code in ("429", "500", "502", "503"))
