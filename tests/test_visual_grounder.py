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
        "The screenshot adds a visual grouping: the toolbar sits above two input fields."
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

    assert parsed["alt_text"].startswith("The screenshot adds a visual grouping")
    assert parsed["confidence"] == 0.5
    assert llm.image_calls == [[screenshot]]
    assert llm.text_calls == 0


def test_ground_fsm_visual_annotations_writes_state_annotations(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"placeholder")
    llm = FakeVisualLlm(
        "Element e_7 is visually a paper-plane send icon grouped with the bottom composer."
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
    assert annotations.alt_text == (
        "Element e_7 is visually a paper-plane send icon grouped with the bottom composer."
    )
    assert annotations.page_function == ""
    assert annotations.expected_actions == []
    assert annotations.widget_aliases == []
    assert annotations.generation_confidence == 0.5
