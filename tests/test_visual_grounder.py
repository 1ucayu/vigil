"""Tests for LLM-derived screenshot/layout grounding."""

from __future__ import annotations

from pathlib import Path

from vigil.models.fsm import AbstractState, AppFSM, HierarchyLevel
from vigil.neuro.visual_grounder import (
    describe_screen_visuals,
    ground_fsm_visual_annotations,
)


class FakeVisualLlm:
    def __init__(self, response: str) -> None:
        self.response = response
        self.image_calls: list[list[Path]] = []
        self.text_calls = 0

    def generate_with_images(
        self,
        _system_prompt: str,
        _text_prompt: str,
        images: list[Path],
        _image_labels: list[str] | None = None,
    ) -> str:
        self.image_calls.append(images)
        return self.response

    def generate(self, _system_prompt: str, _user_prompt: str) -> str:
        self.text_calls += 1
        return self.response


def _state(state_id: str, raw_screen_ids: list[str]) -> AbstractState:
    return AbstractState(
        state_id=state_id,
        name=state_id,
        fingerprint=f"fp_{state_id}",
        hierarchy_level=HierarchyLevel.ACTIVITY,
        raw_screens=raw_screen_ids,
    )


def test_describe_screen_visuals_uses_existing_screenshot(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"placeholder")
    llm = FakeVisualLlm(
        """
        ```json
        {
          "alt_text": "A form screen with a toolbar.",
          "layout_summary": "Toolbar above two input fields.",
          "page_function": "banking/transfer/form",
          "expected_actions": ["enter_amount"],
          "icon_labels": [],
          "confidence": 0.7
        }
        ```
        """
    )

    parsed = describe_screen_visuals(
        state_id="s1",
        observation={
            "screen_id": "scr_1",
            "screenshot_path": str(screenshot),
            "interactable_elements": [],
        },
        llm=llm,  # type: ignore[arg-type]
    )

    assert parsed["page_function"] == "banking/transfer/form"
    assert llm.image_calls == [[screenshot]]
    assert llm.text_calls == 0


def test_ground_fsm_visual_annotations_writes_state_annotations(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"placeholder")
    llm = FakeVisualLlm(
        """
        Result:
        {
          "alt_text": "A chat thread with messages and a bottom composer.",
          "layout_summary": "Message list above text input and send icon.",
          "page_function": "chat/thread",
          "expected_actions": ["type_message", "send_message"],
          "icon_labels": [
            {
              "element_id": "e_7",
              "label": "send button",
              "confidence": 0.91,
              "basis": "paper-plane icon next to composer"
            }
          ],
          "confidence": 0.86
        }
        """
    )
    fsm = AppFSM("com.test")
    fsm.add_state(_state("s1", ["scr_1"]))

    report = ground_fsm_visual_annotations(
        fsm,
        {
            "scr_1": {
                "screen_id": "scr_1",
                "screenshot_path": str(screenshot),
                "interactable_elements": [
                    {
                        "element_id": "e_7",
                        "class_name": "android.widget.ImageButton",
                        "bounds": [1, 2, 3, 4],
                        "is_clickable": True,
                    }
                ],
            }
        },
        llm,  # type: ignore[arg-type]
    )

    assert report[0]["status"] == "annotated"
    annotations = fsm.states["s1"].annotations
    assert annotations.page_function == "chat/thread"
    assert annotations.expected_actions == ["type_message", "send_message"]
    assert annotations.widget_aliases == [
        {
            "element_id": "e_7",
            "label": "send_button",
            "confidence": 0.91,
            "basis": "paper-plane icon next to composer",
        }
    ]
    assert annotations.generation_confidence == 0.86
