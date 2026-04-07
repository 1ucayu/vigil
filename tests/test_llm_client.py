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
