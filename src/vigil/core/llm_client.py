"""Unified LLM client wrapper (Google Gemini / Anthropic / Proxy).

Used ONLY during offline stages (state abstraction, DSL generation, Tier 3 evolution).
The online symbolic verifier must NEVER call this client for Tier 1-2.
"""

from __future__ import annotations

import base64
import io
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from loguru import logger
from PIL import Image

from vigil.core.config import LLMConfig

load_dotenv()

_MAX_IMAGE_EDGE = 1280
_JPEG_QUALITY = 85
_MAX_RETRIES = 3
_BACKOFF_BASE = 1  # seconds


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
            contents: list[Any] = []
            for label, img in zip(labels, pil_images, strict=True):
                if label:
                    contents.append(label)
                contents.append(img)
            contents.append(text_prompt)
            return self._generate_google(system_prompt, text_prompt, contents=contents)

        return self._generate_anthropic_with_images(system_prompt, text_prompt, pil_images, labels)

    def _generate_google(self, system_prompt: str, _text_prompt: str, contents: list[Any]) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            systemInstruction=system_prompt,
            maxOutputTokens=self._config.max_tokens,
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
            return self._client.messages.create(
                model=self._model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            )

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

        def _call() -> Any:
            return self._client.messages.create(
                model=self._model,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            )

        response = self._call_with_retry(_call)
        if hasattr(response, "usage"):
            logger.debug(
                f"Anthropic tokens: input={response.usage.input_tokens}, "
                f"output={response.usage.output_tokens}"
            )
        return response.content[0].text

    def _generate_openai_compatible(self, system_prompt: str, user_prompt: str) -> str:
        def _call() -> Any:
            return self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            )

        response = self._call_with_retry(_call)
        if hasattr(response, "usage") and response.usage:
            logger.debug(
                f"OpenAI-compatible tokens: input={response.usage.prompt_tokens}, "
                f"output={response.usage.completion_tokens}"
            )
        return response.choices[0].message.content or ""

    def _generate_openai_compatible_with_images(
        self,
        system_prompt: str,
        text_prompt: str,
        images: list[Path],
        image_labels: list[str] | None = None,
    ) -> str:
        """Multimodal generation via OpenAI-compatible proxy with base64 images."""
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

        def _call() -> Any:
            return self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            )

        response = self._call_with_retry(_call)
        if hasattr(response, "usage") and response.usage:
            logger.debug(
                f"OpenAI-compatible tokens: input={response.usage.prompt_tokens}, "
                f"output={response.usage.completion_tokens}"
            )
        return response.choices[0].message.content or ""

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
