"""CLI entry point: vigil-visualize.

Visualizes a constructed FSM as a graph image using Graphviz.

Usage:
    vigil-visualize --fsm <fsm.json> --output <output.png>
    vigil-visualize --fsm <fsm.json> --output <output.svg> --format svg
"""

from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger

# Action type → edge color mapping
_ACTION_COLORS: dict[str, str] = {
    "click": "#333333",
    "long_press": "#2e7d32",
    "scroll_up": "#1565c0",
    "scroll_down": "#1565c0",
    "navigate_back": "#c62828",
    "navigate_home": "#c62828",
    "input_text": "#f57f17",
}

# Color palette for activity grouping
_ACTIVITY_COLORS = [
    "#e3f2fd",
    "#fce4ec",
    "#e8f5e9",
    "#fff3e0",
    "#f3e5f5",
    "#e0f7fa",
    "#fff9c4",
    "#fbe9e7",
    "#ede7f6",
    "#e8eaf6",
]


def render_fsm(
    fsm_path: Path,
    output_path: Path,
    fmt: str = "png",
    layout: str = "dot",
    show_counts: bool = False,
    max_label_len: int = 25,
) -> None:
    """Render an FSM to a graph image.

    Args:
        fsm_path: Path to serialized FSM JSON.
        output_path: Output file path.
        fmt: Output format (png, svg, pdf).
        layout: Graphviz layout engine (dot, neato, fdp, sfdp).
        show_counts: Show observed_count on edge labels.
        max_label_len: Max characters for node/edge labels.
    """
    try:
        import graphviz
    except ImportError as err:
        logger.error(
            "graphviz package not installed. Install with: uv pip install graphviz\n"
            "Also need system binary: brew install graphviz"
        )
        raise SystemExit(1) from err

    from vigil.models.fsm import AppFSM, ContainerType

    fsm = AppFSM.deserialize(fsm_path)
    logger.info(f"Loaded {fsm}")

    dot = graphviz.Digraph(
        name=fsm.app_package,
        format=fmt,
        engine=layout,
    )
    dot.attr(
        rankdir="LR",
        fontname="Helvetica",
        fontsize="11",
        bgcolor="white",
        pad="0.5",
    )
    dot.attr("node", fontname="Helvetica", fontsize="10", style="filled")
    dot.attr("edge", fontname="Helvetica", fontsize="8")

    # Assign colors by activity
    activities = sorted({s.activity_name or "unknown" for s in fsm.states.values()})
    activity_color = {
        act: _ACTIVITY_COLORS[i % len(_ACTIVITY_COLORS)] for i, act in enumerate(activities)
    }

    # Container type → visual style
    container_style = {
        ContainerType.CONTENT: {"fillcolor": "#c8e6c9", "suffix": "[C]"},
        ContainerType.STRUCTURAL: {"fillcolor": "#bbdefb", "suffix": "[S]"},
    }

    # Add nodes
    for state in fsm.states.values():
        label = _truncate(state.name, max_label_len)
        if state.activity_name:
            label += f"\n({state.activity_name.rsplit('.', 1)[-1]})"

        # Annotate container type
        ct_style = container_style.get(state.container_type)
        if ct_style:
            label += f"\n{ct_style['suffix']}"

        shape = "box" if state.hierarchy_level == "activity" else "ellipse"
        fill = activity_color.get(state.activity_name or "unknown", "#e3f2fd")
        if ct_style:
            fill = ct_style["fillcolor"]

        peripheries = "2" if state.state_id == fsm.initial_state else "1"

        dot.node(
            state.state_id,
            label=label,
            shape=shape,
            fillcolor=fill,
            peripheries=peripheries,
        )

    # Add edges
    for t in fsm.transitions:
        action_type = t.action.get("type", "?")
        color = _ACTION_COLORS.get(action_type, "#666666")

        label = action_type
        if show_counts and t.observed_count > 1:
            label += f" ({t.observed_count})"

        dot.edge(
            t.source,
            t.target,
            label=label,
            color=color,
            fontcolor=color,
        )

    # Render
    out_stem = str(output_path).removesuffix(f".{fmt}")
    dot.render(out_stem, cleanup=True)
    logger.info(f"FSM graph rendered to {output_path}")


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 2] + ".."


def main() -> None:
    """Run the FSM visualization pipeline."""
    parser = argparse.ArgumentParser(
        prog="vigil-visualize",
        description="Visualize an FSM as a graph image.",
    )
    parser.add_argument(
        "--fsm",
        required=True,
        help="Path to serialized FSM JSON file",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output image path (e.g., docs/settings_fsm.png)",
    )
    parser.add_argument(
        "--format",
        choices=["png", "svg", "pdf"],
        default="png",
        help="Output format (default: png)",
    )
    parser.add_argument(
        "--layout",
        choices=["dot", "neato", "fdp", "sfdp"],
        default="dot",
        help="Graphviz layout engine (default: dot)",
    )
    parser.add_argument(
        "--show-counts",
        action="store_true",
        help="Show observed_count on edge labels",
    )
    parser.add_argument(
        "--max-label-len",
        type=int,
        default=25,
        help="Max characters for labels (default: 25)",
    )

    args = parser.parse_args()

    fsm_path = Path(args.fsm)
    if not fsm_path.exists():
        logger.error(f"FSM file not found: {fsm_path}")
        raise SystemExit(1)

    render_fsm(
        fsm_path=fsm_path,
        output_path=Path(args.output),
        fmt=args.format,
        layout=args.layout,
        show_counts=args.show_counts,
        max_label_len=args.max_label_len,
    )


if __name__ == "__main__":
    main()
