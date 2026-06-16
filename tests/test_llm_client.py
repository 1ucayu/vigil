"""Tests for vigil.core.llm_client — LLM client with retry and image handling."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from pydantic import BaseModel

from vigil.core.config import LLMConfig
from vigil.core.llm_client import LlmClient, _ProxyStructuredStrategy


class TestGoogleInit:
    """Test Google provider initialization."""

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key-123"})
    @patch("vigil.core.llm_client.genai", create=True)
    def test_google_provider_init(self, mock_genai: MagicMock) -> None:
        # Patch the import inside __init__
        with patch("google.genai.Client") as mock_client_cls:
            config = LLMConfig(provider="google", model="gemini-2.0-flash")
            client = LlmClient(config)
            mock_client_cls.assert_called_once_with(api_key="test-key-123")
            assert client._model == "gemini-2.0-flash"

    def test_missing_api_key_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            # Remove GOOGLE_API_KEY if present
            os.environ.pop("GOOGLE_API_KEY", None)
            config = LLMConfig(provider="google", model="gemini-2.0-flash")
            with pytest.raises(ValueError, match="Missing API key"):
                LlmClient(config)


class TestGenerate:
    """Test text generation."""

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"})
    def test_generate_text_google(self) -> None:
        with patch("google.genai.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_response = MagicMock()
            mock_response.text = "read(btn, enabled) == true"
            mock_response.usage_metadata = None
            mock_client.models.generate_content.return_value = mock_response

            config = LLMConfig(provider="google", model="gemini-2.0-flash")
            client = LlmClient(config)
            result = client.generate("system", "user prompt")

            assert result == "read(btn, enabled) == true"
            mock_client.models.generate_content.assert_called_once()

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"})
    def test_generate_passes_system_instruction(self) -> None:
        with patch("google.genai.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_response = MagicMock()
            mock_response.text = "null"
            mock_response.usage_metadata = None
            mock_client.models.generate_content.return_value = mock_response

            config = LLMConfig(provider="google", model="gemini-2.0-flash")
            client = LlmClient(config)
            client.generate("my system prompt", "user prompt")

            call_kwargs = mock_client.models.generate_content.call_args
            gen_config = call_kwargs.kwargs["config"]
            assert gen_config.system_instruction == "my system prompt"


class TestAnthropicProvider:
    """Test Anthropic provider initialization."""

    @patch("anthropic.Anthropic")
    def test_anthropic_init_uses_local_service_by_default(
        self, mock_anthropic_cls: MagicMock
    ) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = LLMConfig(
                provider="anthropic",
                model="claude-sonnet-4.6",
            )
            client = LlmClient(config)

        mock_anthropic_cls.assert_called_once_with(
            api_key="dummy_key",
            base_url="http://localhost:4141",
        )
        assert client._model == "claude-sonnet-4.6"

    @patch.dict(
        os.environ,
        {"ANTHROPIC_API_KEY": "env-key", "ANTHROPIC_BASE_URL": "http://example.test"},
    )
    @patch("anthropic.Anthropic")
    def test_anthropic_init_env_overrides_config(self, mock_anthropic_cls: MagicMock) -> None:
        config = LLMConfig(
            provider="anthropic",
            anthropic_api_key="config-key",
            anthropic_base_url="http://localhost:4141",
        )
        LlmClient(config)

        mock_anthropic_cls.assert_called_once_with(
            api_key="env-key",
            base_url="http://example.test",
        )

    @patch("anthropic.Anthropic")
    def test_anthropic_generate_omits_sampling_but_includes_required_max_tokens(
        self, mock_anthropic_cls: MagicMock
    ) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client
            text_block = MagicMock(text="ok")
            mock_client.messages.create.return_value = MagicMock(
                content=[text_block],
                usage=None,
            )

            client = LlmClient(LLMConfig(provider="anthropic", model="claude-sonnet-4.6"))
            assert client.generate("sys", "user") == "ok"

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert "temperature" not in call_kwargs
            assert "max_tokens" in call_kwargs


class TestOpenAIProvider:
    """Test OpenAI-compatible provider initialization."""

    @patch("openai.OpenAI")
    def test_openai_init_uses_local_v1_service_by_default(self, mock_openai_cls: MagicMock) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = LLMConfig(
                provider="openai",
                model="gpt-4.1",
            )
            client = LlmClient(config)

        mock_openai_cls.assert_called_once_with(
            base_url="http://localhost:4141/v1",
            api_key="dummy_key",
        )
        assert client._model == "gpt-4.1"

    @patch.dict(
        os.environ,
        {"OPENAI_API_KEY": "env-key", "OPENAI_BASE_URL": "http://example.test/v1"},
    )
    @patch("openai.OpenAI")
    def test_openai_init_env_overrides_config(self, mock_openai_cls: MagicMock) -> None:
        config = LLMConfig(
            provider="openai",
            openai_api_key="config-key",
            openai_base_url="http://localhost:4141/v1",
        )
        LlmClient(config)

        mock_openai_cls.assert_called_once_with(
            base_url="http://example.test/v1",
            api_key="env-key",
        )


class TestRetry:
    """Test retry logic."""

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"})
    def test_retry_on_rate_limit(self) -> None:
        with patch("google.genai.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            rate_err = Exception("429 rate limit exceeded")
            mock_response = MagicMock()
            mock_response.text = "ok"
            mock_response.usage_metadata = None
            mock_client.models.generate_content.side_effect = [
                rate_err,
                rate_err,
                mock_response,
            ]

            config = LLMConfig(provider="google", model="gemini-2.0-flash")
            client = LlmClient(config)

            with patch("vigil.core.llm_client.time.sleep"):
                result = client.generate("sys", "user")

            assert result == "ok"
            assert mock_client.models.generate_content.call_count == 3

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"})
    def test_all_retries_exhausted(self) -> None:
        with patch("google.genai.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            rate_err = Exception("429 rate limit")
            mock_client.models.generate_content.side_effect = [
                rate_err,
                rate_err,
                rate_err,
            ]

            config = LLMConfig(provider="google", model="gemini-2.0-flash")
            client = LlmClient(config)

            with patch("vigil.core.llm_client.time.sleep"), pytest.raises(Exception, match="429"):
                client.generate("sys", "user")


class TestImagePreprocessing:
    """Test image resize and conversion."""

    def test_resize_large_image(self) -> None:
        img_path = self._create_temp_image(2000, 1500)
        result = LlmClient._preprocess_image(img_path)
        assert max(result.size) <= 1280
        # Check aspect ratio preserved
        assert abs(result.size[0] / result.size[1] - 2000 / 1500) < 0.01

    def test_small_image_unchanged(self) -> None:
        img_path = self._create_temp_image(640, 480)
        result = LlmClient._preprocess_image(img_path)
        assert result.size == (640, 480)

    def test_rgba_to_rgb(self) -> None:
        img_path = self._create_temp_image(100, 100, mode="RGBA")
        result = LlmClient._preprocess_image(img_path)
        assert result.mode == "RGB"

    @staticmethod
    def _create_temp_image(w: int, h: int, mode: str = "RGB") -> Path:
        img = Image.new(mode, (w, h), color=(128, 128, 128))
        path = Path(tempfile.mktemp(suffix=".png"))
        img.save(path)
        return path


class TestGenerateWithImages:
    """Test multimodal generation."""

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"})
    def test_generate_with_images_google(self, tmp_path: Path) -> None:
        # Create test images
        img1 = Image.new("RGB", (100, 100), color=(255, 0, 0))
        img2 = Image.new("RGB", (100, 100), color=(0, 255, 0))
        p1 = tmp_path / "source.png"
        p2 = tmp_path / "target.png"
        img1.save(p1)
        img2.save(p2)

        with patch("google.genai.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_response = MagicMock()
            mock_response.text = "null"
            mock_response.usage_metadata = None
            mock_client.models.generate_content.return_value = mock_response

            config = LLMConfig(provider="google", model="gemini-2.0-flash")
            client = LlmClient(config)
            result = client.generate_with_images(
                "system",
                "text prompt",
                images=[p1, p2],
                image_labels=["Source:", "Target:"],
            )

            assert result == "null"
            call_kwargs = mock_client.models.generate_content.call_args
            contents = call_kwargs.kwargs["contents"]
            # Should have: label, image, label, image, text_prompt
            assert len(contents) == 5
            assert contents[0] == "Source:"
            assert isinstance(contents[1], Image.Image)
            assert contents[2] == "Target:"
            assert isinstance(contents[3], Image.Image)
            assert contents[4] == "text prompt"


class TestProxyProvider:
    """Test proxy provider (OpenAI-compatible local API)."""

    @patch("openai.OpenAI")
    def test_proxy_init(self, mock_openai_cls: MagicMock) -> None:
        config = LLMConfig(
            provider="proxy",
            proxy_base_url="http://localhost:4141/v1",
            proxy_api_key="my_key",
            proxy_model="claude-sonnet-4.5",
        )
        client = LlmClient(config)
        mock_openai_cls.assert_called_once_with(
            base_url="http://localhost:4141/v1",
            api_key="my_key",
        )
        assert client._model == "claude-sonnet-4.5"

    @patch("openai.OpenAI")
    def test_proxy_no_env_key_needed(self, mock_openai_cls: MagicMock) -> None:
        """Proxy provider should not require any environment variable."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GOOGLE_API_KEY", None)
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
            config = LLMConfig(provider="proxy")
            LlmClient(config)  # should not raise

    @patch("openai.OpenAI")
    def test_proxy_generate(self, mock_openai_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="guard result"))]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        mock_client.chat.completions.create.return_value = mock_response

        config = LLMConfig(provider="proxy", proxy_model="gpt-5.4")
        client = LlmClient(config)
        metadata = {
            "vendor": "Azure OpenAI",
            "supported_endpoints": ["/chat/completions"],
            "capabilities": {"supports": {"vision": True}},
        }
        with patch.object(client, "_proxy_model_metadata", return_value=metadata):
            result = client.generate("system prompt", "user prompt")

        assert result == "guard result"
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-5.4"
        assert call_kwargs["messages"] == [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user prompt"},
        ]
        assert "max_tokens" not in call_kwargs

    @patch("openai.OpenAI")
    def test_proxy_empty_choices_raises_clear_error(self, mock_openai_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = []
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response

        config = LLMConfig(provider="proxy", proxy_model="gpt-5-mini")
        client = LlmClient(config)
        metadata = {
            "vendor": "Azure OpenAI",
            "supported_endpoints": ["/chat/completions"],
            "capabilities": {"supports": {"vision": True}},
        }

        with (
            patch.object(client, "_proxy_model_metadata", return_value=metadata),
            pytest.raises(ValueError, match="contained no choices"),
        ):
            client.generate("system prompt", "user prompt")

    @patch("openai.OpenAI")
    def test_proxy_images_fallback(self, mock_openai_cls: MagicMock, tmp_path: Path) -> None:
        """generate_with_images should send OpenAI-compatible image content."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="text only"))]
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response

        # Create a dummy image
        img = Image.new("RGB", (100, 100))
        img_path = tmp_path / "test.png"
        img.save(img_path)

        config = LLMConfig(provider="proxy", proxy_model="gpt-5-mini")
        client = LlmClient(config)
        metadata = {
            "vendor": "Azure OpenAI",
            "supported_endpoints": ["/chat/completions"],
            "capabilities": {"supports": {"vision": True}},
        }

        with patch.object(client, "_proxy_model_metadata", return_value=metadata):
            result = client.generate_with_images("system", "text prompt", images=[img_path])

        assert result == "text only"
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "max_tokens" not in call_kwargs
        content = call_kwargs["messages"][1]["content"]
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert content[1] == {"type": "text", "text": "text prompt"}

    @patch("openai.OpenAI")
    def test_proxy_claude_generate_uses_messages_endpoint(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.return_value = MagicMock()
        config = LLMConfig(provider="proxy", proxy_model="claude-haiku-4-5")
        client = LlmClient(config)
        metadata = {
            "vendor": "Anthropic",
            "supported_endpoints": ["/v1/messages", "/chat/completions"],
            "capabilities": {
                "supports": {"vision": True, "tool_calls": True},
                "limits": {"max_output_tokens": 1234},
            },
        }
        calls: list[tuple[str, dict]] = []

        def fake_post(path: str, payload: dict) -> dict:
            calls.append((path, payload))
            return {"content": [{"type": "text", "text": "plain result"}]}

        with (
            patch.object(client, "_proxy_model_metadata", return_value=metadata),
            patch.object(client, "_proxy_post_json", side_effect=fake_post),
        ):
            result = client.generate("system prompt", "user prompt")

        assert result == "plain result"
        assert calls[0][0] == "/messages"
        assert calls[0][1]["model"] == "claude-haiku-4-5"
        assert calls[0][1]["max_tokens"] == 1234
        mock_openai_cls.return_value.chat.completions.create.assert_not_called()

    @patch("openai.OpenAI")
    def test_proxy_claude_generate_with_images_uses_messages_endpoint(
        self,
        mock_openai_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_openai_cls.return_value = MagicMock()
        img = Image.new("RGB", (100, 100))
        img_path = tmp_path / "test.png"
        img.save(img_path)
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="claude-haiku-4-5"))
        metadata = {
            "vendor": "Anthropic",
            "supported_endpoints": ["/v1/messages", "/chat/completions"],
            "capabilities": {"supports": {"vision": True, "tool_calls": True}},
        }
        calls: list[tuple[str, dict]] = []

        def fake_post(path: str, payload: dict) -> dict:
            calls.append((path, payload))
            return {"content": [{"type": "text", "text": "image result"}]}

        with (
            patch.object(client, "_proxy_model_metadata", return_value=metadata),
            patch.object(client, "_proxy_post_json", side_effect=fake_post),
        ):
            result = client.generate_with_images("system", "prompt", [img_path], ["img"])

        assert result == "image result"
        assert calls[0][0] == "/messages"
        blocks = calls[0][1]["messages"][0]["content"]
        assert blocks[0] == {"type": "text", "text": "img"}
        assert blocks[1]["type"] == "image"
        assert blocks[2] == {"type": "text", "text": "prompt"}
        mock_openai_cls.return_value.chat.completions.create.assert_not_called()


class _Schema(BaseModel):
    value: str


class _StructuredStrictSchema(BaseModel):
    value: Literal["ok"]


class TestStructuredOutput:
    """Provider-aware structured generation routing (fake clients only)."""

    @patch("openai.OpenAI")
    def test_openai_native_schema_parse(self, mock_openai_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        message = MagicMock(parsed=_Schema(value="ok"), content='{"value":"ok"}', refusal=None)
        completion = MagicMock(choices=[MagicMock(message=message, finish_reason="stop")])
        mock_client.chat.completions.parse.return_value = completion

        client = LlmClient(LLMConfig(provider="openai", model="gpt-5.4"))
        result = client.generate_structured("sys", "user", _Schema, "Sch")

        assert result.parsed == _Schema(value="ok")
        assert result.schema_constraint_mode == "native_schema"
        assert result.refusal is None
        call_kwargs = mock_client.chat.completions.parse.call_args.kwargs
        assert call_kwargs["response_format"] is _Schema
        assert "max_tokens" not in call_kwargs
        assert "max_completion_tokens" not in call_kwargs

    @patch("openai.OpenAI")
    def test_openai_unsupported_schema_fails_clearly_without_fallback(
        self, mock_openai_cls: MagicMock
    ) -> None:
        import httpx
        import openai

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        request = httpx.Request("POST", "http://localhost:4141/v1/chat/completions")
        response = httpx.Response(400, request=request)
        mock_client.chat.completions.parse.side_effect = openai.BadRequestError(
            "response_format json_schema not supported", response=response, body=None
        )

        client = LlmClient(LLMConfig(provider="openai", model="gpt-5.4"))
        result = client.generate_structured("sys", "user", _Schema, "Sch")

        assert result.parsed is None
        assert result.schema_constraint_mode == "prompt_only_unavailable"
        assert result.validation_errors
        # The plain text path must NOT be used when fallback is not opted into.
        mock_client.chat.completions.create.assert_not_called()

    @patch("openai.OpenAI")
    def test_openai_unsupported_schema_uses_fallback_when_opted_in(
        self, mock_openai_cls: MagicMock
    ) -> None:
        import httpx
        import openai

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        request = httpx.Request("POST", "http://localhost:4141/v1/chat/completions")
        response = httpx.Response(400, request=request)
        mock_client.chat.completions.parse.side_effect = openai.BadRequestError(
            "json_schema unsupported", response=response, body=None
        )
        # fallback_validate routes through the plain chat.completions.create text path.
        text_completion = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"value":"fallback"}'))], usage=None
        )
        mock_client.chat.completions.create.return_value = text_completion

        client = LlmClient(LLMConfig(provider="openai", model="gpt-5.4"))
        result = client.generate_structured(
            "sys", "user", _Schema, "Sch", allow_provider_fallback=True
        )

        assert result.schema_constraint_mode == "fallback_validate"
        assert result.parsed == _Schema(value="fallback")
        mock_client.chat.completions.create.assert_called_once()

    @patch("openai.OpenAI")
    def test_proxy_anthropic_messages_tool(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.return_value = MagicMock()
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="claude-haiku-4-5"))
        metadata = {
            "vendor": "Anthropic",
            "supported_endpoints": ["/v1/messages"],
            "capabilities": {
                "supports": {"tool_calls": True, "vision": True},
                "limits": {"max_output_tokens": 12345},
            },
        }
        calls: list[tuple[str, dict]] = []

        def fake_post(path: str, payload: dict) -> dict:
            calls.append((path, payload))
            return {
                "content": [{"type": "tool_use", "input": {"value": "ok"}}],
                "stop_reason": "tool_use",
            }

        with (
            patch.object(client, "_proxy_model_metadata", return_value=metadata),
            patch.object(client, "_proxy_probe_strategy", return_value=MagicMock(ok=True)),
            patch.object(client, "_proxy_post_json", side_effect=fake_post),
        ):
            result = client.generate_structured("sys", "user", _Schema, "Sch")

        assert result.parsed == _Schema(value="ok")
        assert result.schema_constraint_mode == "tool_schema"
        assert result.strategy == "anthropic_messages_tool"
        assert result.transport == "/v1/messages"
        path, payload = calls[0]
        assert path == "/messages"
        assert payload["tool_choice"] == {"type": "tool", "name": "Sch"}
        assert payload["tools"][0]["strict"] is True
        assert payload["max_tokens"] == 12345
        assert "temperature" not in payload

    @patch("openai.OpenAI")
    def test_proxy_google_chat_function_tool(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.return_value = MagicMock()
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="gemini-3.5-flash"))
        metadata = {
            "vendor": "Google",
            "supported_endpoints": ["/chat/completions"],
            "capabilities": {"supports": {"tool_calls": True, "vision": True}},
        }
        calls: list[tuple[str, dict]] = []

        def fake_post(path: str, payload: dict) -> dict:
            calls.append((path, payload))
            return {
                "choices": [
                    {
                        "message": {"tool_calls": [{"function": {"arguments": '{"value":"ok"}'}}]},
                        "finish_reason": "stop",
                    }
                ]
            }

        with (
            patch.object(client, "_proxy_model_metadata", return_value=metadata),
            patch.object(client, "_proxy_probe_strategy", return_value=MagicMock(ok=True)),
            patch.object(client, "_proxy_post_json", side_effect=fake_post),
        ):
            result = client.generate_structured("sys", "user", _Schema, "Sch")

        assert result.parsed == _Schema(value="ok")
        assert result.schema_constraint_mode == "tool_schema"
        assert result.strategy == "chat_function_tool"
        path, payload = calls[0]
        assert path == "/chat/completions"
        assert payload["tool_choice"] == {"type": "function", "function": {"name": "Sch"}}
        assert payload["tools"][0]["function"]["strict"] is True
        assert "temperature" not in payload

    @patch("openai.OpenAI")
    def test_proxy_openai_responses_json_schema(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.return_value = MagicMock()
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="gpt-5.4"))
        metadata = {
            "vendor": "Azure OpenAI",
            "supported_endpoints": ["/responses"],
            "capabilities": {"supports": {"structured_outputs": True, "vision": True}},
        }
        calls: list[tuple[str, dict]] = []

        def fake_post(path: str, payload: dict) -> dict:
            calls.append((path, payload))
            return {"output_text": '{"value":"ok"}', "status": "completed"}

        with (
            patch.object(client, "_proxy_model_metadata", return_value=metadata),
            patch.object(client, "_proxy_probe_strategy", return_value=MagicMock(ok=True)),
            patch.object(client, "_proxy_post_json", side_effect=fake_post),
        ):
            result = client.generate_structured("sys", "user", _Schema, "Sch")

        assert result.parsed == _Schema(value="ok")
        assert result.schema_constraint_mode == "native_schema"
        assert result.strategy == "responses_json_schema"
        path, payload = calls[0]
        assert path == "/responses"
        assert payload["text"]["format"]["strict"] is True
        assert payload["text"]["format"]["name"] == "Sch"
        assert "temperature" not in payload

    @patch("openai.OpenAI")
    def test_proxy_openai_responses_json_schema_explicit_temperature(
        self, mock_openai_cls: MagicMock
    ) -> None:
        mock_openai_cls.return_value = MagicMock()
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="gpt-5.4", temperature=0.0))
        metadata = {
            "vendor": "Azure OpenAI",
            "supported_endpoints": ["/responses"],
            "capabilities": {"supports": {"structured_outputs": True, "vision": True}},
        }
        calls: list[tuple[str, dict]] = []

        def fake_post(path: str, payload: dict) -> dict:
            calls.append((path, payload))
            return {"output_text": '{"value":"ok"}', "status": "completed"}

        with (
            patch.object(client, "_proxy_model_metadata", return_value=metadata),
            patch.object(client, "_proxy_probe_strategy", return_value=MagicMock(ok=True)),
            patch.object(client, "_proxy_post_json", side_effect=fake_post),
        ):
            client.generate_structured("sys", "user", _Schema, "Sch")

        assert calls[0][1]["temperature"] == 0.0

    @patch("openai.OpenAI")
    def test_proxy_openai_chat_json_schema(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.return_value = MagicMock()
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="gpt-5-mini"))
        metadata = {
            "vendor": "Azure OpenAI",
            "supported_endpoints": ["/chat/completions"],
            "capabilities": {"supports": {"structured_outputs": True, "vision": True}},
        }
        calls: list[tuple[str, dict]] = []

        def fake_post(path: str, payload: dict) -> dict:
            calls.append((path, payload))
            return {
                "choices": [{"message": {"content": '{"value":"ok"}'}, "finish_reason": "stop"}]
            }

        with (
            patch.object(client, "_proxy_model_metadata", return_value=metadata),
            patch.object(client, "_proxy_probe_strategy", return_value=MagicMock(ok=True)),
            patch.object(client, "_proxy_post_json", side_effect=fake_post),
        ):
            result = client.generate_structured("sys", "user", _Schema, "Sch")

        assert result.parsed == _Schema(value="ok")
        assert result.schema_constraint_mode == "native_schema"
        assert result.strategy == "chat_json_schema"
        path, payload = calls[0]
        assert path == "/chat/completions"
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert "temperature" not in payload

    @patch("openai.OpenAI")
    def test_proxy_native_unenforced_fails_without_repair(self, mock_openai_cls: MagicMock) -> None:
        mock_openai_cls.return_value = MagicMock()
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="gpt-5-mini"))
        metadata = {
            "vendor": "Azure OpenAI",
            "supported_endpoints": ["/chat/completions"],
            "capabilities": {"supports": {"structured_outputs": True, "vision": True}},
        }

        def fake_post(_path: str, _payload: dict) -> dict:
            return {
                "choices": [
                    {
                        "message": {"content": '```json\n{"value":"bad"}\n```'},
                        "finish_reason": "stop",
                    }
                ]
            }

        with (
            patch.object(client, "_proxy_model_metadata", return_value=metadata),
            patch.object(client, "_proxy_probe_strategy", return_value=MagicMock(ok=True)),
            patch.object(client, "_proxy_post_json", side_effect=fake_post),
        ):
            result = client.generate_structured("sys", "user", _StructuredStrictSchema, "Sch")

        assert result.parsed is None
        assert result.schema_constraint_mode == "native_schema_unenforced"
        assert result.validation_errors

    @patch("openai.OpenAI")
    def test_proxy_tool_schema_invalid_arguments_are_rejected(
        self, mock_openai_cls: MagicMock
    ) -> None:
        mock_openai_cls.return_value = MagicMock()
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="gemini-3.5-flash"))
        metadata = {
            "vendor": "Google",
            "supported_endpoints": ["/chat/completions"],
            "capabilities": {"supports": {"tool_calls": True, "vision": True}},
        }

        def fake_post(_path: str, _payload: dict) -> dict:
            return {
                "choices": [
                    {
                        "message": {"tool_calls": [{"function": {"arguments": '{"value":"bad"}'}}]},
                        "finish_reason": "stop",
                    }
                ]
            }

        with (
            patch.object(client, "_proxy_model_metadata", return_value=metadata),
            patch.object(client, "_proxy_probe_strategy", return_value=MagicMock(ok=True)),
            patch.object(client, "_proxy_post_json", side_effect=fake_post),
        ):
            result = client.generate_structured("sys", "user", _StructuredStrictSchema, "Sch")

        assert result.parsed is None
        assert result.schema_constraint_mode == "tool_schema"
        assert result.strategy == "chat_function_tool"
        assert result.validation_errors

    @patch("openai.OpenAI")
    def test_proxy_probe_rejects_unenforced_chat_json_schema(
        self, mock_openai_cls: MagicMock
    ) -> None:
        mock_openai_cls.return_value = MagicMock()
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="gpt-5-mini"))
        strategy = _ProxyStructuredStrategy(
            "chat_json_schema",
            "/chat/completions",
            "native_schema",
        )

        def fake_post(_path: str, _payload: dict) -> dict:
            return {
                "choices": [
                    {
                        "message": {"content": '{"value":"bad"}'},
                        "finish_reason": "stop",
                    }
                ]
            }

        with patch.object(client, "_proxy_post_json", side_effect=fake_post):
            status = client._proxy_probe_strategy(strategy, {"vendor": "Azure OpenAI"})

        assert status.ok is False
        assert "Input should be 'ok'" in status.detail

    @patch("openai.OpenAI")
    def test_proxy_no_strategy_fails_clearly_without_fallback(
        self,
        mock_openai_cls: MagicMock,
    ) -> None:
        mock_openai_cls.return_value = MagicMock()
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="text-only"))
        metadata = {
            "vendor": "Unknown",
            "supported_endpoints": ["/chat/completions"],
            "capabilities": {"supports": {"vision": True}},
        }

        with patch.object(client, "_proxy_model_metadata", return_value=metadata):
            result = client.generate_structured("sys", "user", _Schema, "Sch")

        assert result.parsed is None
        assert result.schema_constraint_mode == "prompt_only_unavailable"
        assert result.validation_errors

    @patch("openai.OpenAI")
    def test_proxy_no_strategy_uses_fallback_when_opted_in(
        self,
        mock_openai_cls: MagicMock,
    ) -> None:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        text_completion = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"value":"fallback"}'))], usage=None
        )
        mock_client.chat.completions.create.return_value = text_completion
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="text-only"))
        metadata = {
            "vendor": "Unknown",
            "supported_endpoints": ["/chat/completions"],
            "capabilities": {"supports": {"vision": True}},
        }

        with patch.object(client, "_proxy_model_metadata", return_value=metadata):
            result = client.generate_structured(
                "sys", "user", _Schema, "Sch", allow_provider_fallback=True
            )

        assert result.schema_constraint_mode == "fallback_validate"
        assert result.parsed == _Schema(value="fallback")
        mock_client.chat.completions.create.assert_called_once()

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "k"})
    def test_google_native_response_schema(self) -> None:
        with patch("google.genai.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            candidate = MagicMock()
            candidate.finish_reason = MagicMock(value="STOP")
            response = MagicMock(parsed=_Schema(value="ok"), text='{"value":"ok"}')
            response.candidates = [candidate]
            mock_client.models.generate_content.return_value = response

            client = LlmClient(LLMConfig(provider="google", model="gemini-2.5-pro"))
            result = client.generate_structured("sys", "user", _Schema, "Sch")

            assert result.parsed == _Schema(value="ok")
            assert result.schema_constraint_mode == "native_schema"
            gen_config = mock_client.models.generate_content.call_args.kwargs["config"]
            assert gen_config.response_schema is _Schema
            assert gen_config.response_mime_type == "application/json"

    @patch("anthropic.Anthropic")
    def test_anthropic_forced_tool_use(self, mock_anthropic_cls: MagicMock) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client
            block = MagicMock()
            block.type = "tool_use"
            block.input = {"value": "ok"}
            message = MagicMock(content=[block], stop_reason="tool_use")
            mock_client.messages.create.return_value = message

            client = LlmClient(LLMConfig(provider="anthropic", model="claude-sonnet-4.6"))
            result = client.generate_structured("sys", "user", _Schema, "Sch")

            assert result.parsed == _Schema(value="ok")
            assert result.schema_constraint_mode == "tool_schema"
            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["tool_choice"] == {"type": "tool", "name": "Sch"}
            assert call_kwargs["tools"][0]["name"] == "Sch"
            # Anthropic REQUIRES max_tokens; it is the one documented cap.
            assert "max_tokens" in call_kwargs

    @patch("openai.OpenAI")
    def test_proxy_refusal_yields_no_parsed(self, mock_openai_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        client = LlmClient(LLMConfig(provider="proxy", proxy_model="gpt-5-mini"))
        metadata = {
            "vendor": "Azure OpenAI",
            "supported_endpoints": ["/chat/completions"],
            "capabilities": {"supports": {"structured_outputs": True, "vision": True}},
        }

        def fake_post(_path: str, _payload: dict) -> dict:
            return {
                "choices": [
                    {
                        "message": {"content": None, "refusal": "I refuse"},
                        "finish_reason": "stop",
                    }
                ]
            }

        with (
            patch.object(client, "_proxy_model_metadata", return_value=metadata),
            patch.object(client, "_proxy_probe_strategy", return_value=MagicMock(ok=True)),
            patch.object(client, "_proxy_post_json", side_effect=fake_post),
        ):
            result = client.generate_structured("sys", "user", _Schema, "Sch")

        assert result.parsed is None
        assert result.refusal == "I refuse"
