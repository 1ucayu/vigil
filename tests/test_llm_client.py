"""Tests for vigil.core.llm_client — LLM client with retry and image handling."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from vigil.core.config import LLMConfig
from vigil.core.llm_client import LlmClient


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

        config = LLMConfig(provider="proxy")
        client = LlmClient(config)

        with pytest.raises(ValueError, match="contained no choices"):
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

        config = LLMConfig(provider="proxy")
        client = LlmClient(config)

        result = client.generate_with_images("system", "text prompt", images=[img_path])

        assert result == "text only"
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "max_tokens" not in call_kwargs
        content = call_kwargs["messages"][1]["content"]
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert content[1] == {"type": "text", "text": "text prompt"}
