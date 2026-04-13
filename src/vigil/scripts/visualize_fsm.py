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

_ACTION_COLORS: dict[str, str] = {
    "click": "#4a4a4a",
    "long_press": "#2e7d32",
    "scroll_up": "#1565c0",
    "scroll_down": "#1565c0",
    "navigate_back": "#b71c1c",
    "navigate_home": "#b71c1c",
    "input_text": "#e65100",
}

_ACTIVITY_COLORS = [
    "#dcedc8",
    "#b3e5fc",
    "#f8bbd0",
    "#fff9c4",
    "#d1c4e9",
    "#ffe0b2",
    "#b2dfdb",
    "#ffccbc",
    "#c5cae9",
    "#f0f4c3",
]

_INITIAL_COLOR = "#1565c0"
_INITIAL_FONT_COLOR = "white"


def render_fsm(
    fsm_path: Path,
    output_path: Path,
    fmt: str = "png",
    layout: str = "dot",
    show_guards: bool = False,
    show_counts: bool = False,
    cluster_activities: bool = True,
    max_label_len: int = 20,
) -> None:
    """Render an FSM to a graph image."""
    try:
        import graphviz
    except ImportError as err:
        logger.error(
            "graphviz package not installed. Install with: uv add graphviz\n"
            "Also need system binary: brew install graphviz"
        )
        raise SystemExit(1) from err

    from vigil.models.fsm import AppFSM

    fsm = AppFSM.deserialize(fsm_path)
    logger.info(f"Loaded {fsm}")

    dot = graphviz.Digraph(
        name=fsm.app_package,
        format=fmt,
        engine=layout,
    )
    dot.attr(
        rankdir="TB",
        fontname="Helvetica Neue,Helvetica,Arial,sans-serif",
        fontsize="12",
        bgcolor="white",
        pad="0.8",
        nodesep="0.6",
        ranksep="0.8",
        label=(
            f"\\n{fsm.app_package}  ({len(fsm.states)} states, {len(fsm.transitions)} transitions)"
        ),
        labelloc="t",
        labeljust="l",
    )
    dot.attr(
        "node",
        fontname="Helvetica Neue,Helvetica,Arial,sans-serif",
        fontsize="10",
        style="filled,rounded",
        shape="box",
        margin="0.15,0.08",
        penwidth="1.2",
    )
    dot.attr(
        "edge",
        fontname="Helvetica Neue,Helvetica,Arial,sans-serif",
        fontsize="8",
        arrowsize="0.7",
        penwidth="1.0",
    )

    activities = sorted({s.activity_name or "" for s in fsm.states.values()})
    activity_color = {
        act: _ACTIVITY_COLORS[i % len(_ACTIVITY_COLORS)] for i, act in enumerate(activities)
    }

    activity_states: dict[str, list[str]] = {}
    for state in fsm.states.values():
        act = state.activity_name or ""
        if act not in activity_states:
            activity_states[act] = []
        activity_states[act].append(state.state_id)

    def _add_node(parent_graph: graphviz.Digraph, state_id: str) -> None:
        state = fsm.states[state_id]
        name = _truncate(state.name, max_label_len)
        is_initial = state_id == fsm.initial_state

        if is_initial:
            parent_graph.node(
                state_id,
                label=name,
                fillcolor=_INITIAL_COLOR,
                fontcolor=_INITIAL_FONT_COLOR,
                penwidth="2.5",
            )
        else:
            fill = activity_color.get(state.activity_name or "", "#e8e8e8")
            parent_graph.node(state_id, label=name, fillcolor=fill)

    if cluster_activities and len(activities) > 1:
        for act in activities:
            sids = activity_states.get(act, [])
            if not sids:
                continue
            short_act = act.rsplit(".", 1)[-1] if act else "unknown"
            with dot.subgraph(name=f"cluster_{short_act}") as sub:
                sub.attr(
                    label=short_act,
                    style="dashed,rounded",
                    color="#999999",
                    fontsize="9",
                    fontcolor="#666666",
                    margin="12",
                )
                for sid in sids:
                    _add_node(sub, sid)
    else:
        for state_id in fsm.states:
            _add_node(dot, state_id)

    for t in fsm.transitions:
        action_type = t.action.get("type", "?")
        color = _ACTION_COLORS.get(action_type, "#888888")
        is_back = action_type in ("navigate_back", "navigate_home")

        label_parts: list[str] = []
        if not is_back:
            label_parts.append(action_type)
        else:
            label_parts.append("back")

        if show_counts and t.observed_count > 1:
            label_parts.append(f"×{t.observed_count}")
        if show_guards and t.guard:
            guard_short = _truncate(t.guard, 30)
            label_parts.append(f"[{guard_short}]")

        label = " ".join(label_parts)

        style = "dashed" if is_back else "solid"
        dot.edge(
            t.source,
            t.target,
            label=label,
            color=color,
            fontcolor=color,
            style=style,
        )

    out_stem = str(output_path).removesuffix(f".{fmt}")
    dot.render(out_stem, cleanup=True)
    logger.info(f"FSM graph rendered to {output_path}")


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def main() -> None:
    """Run the FSM visualization pipeline."""
    parser = argparse.ArgumentParser(
        prog="vigil-visualize",
        description="Visualize an FSM as a graph image.",
    )
    parser.add_argument("--fsm", required=True, help="Path to serialized FSM JSON file")
    parser.add_argument("--output", required=True, help="Output image path")
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
        "--show-guards", action="store_true", help="Show guard expressions on edges"
    )
    parser.add_argument("--show-counts", action="store_true", help="Show observed_count on edges")
    parser.add_argument(
        "--no-cluster",
        action="store_true",
        help="Don't cluster states by Activity",
    )
    parser.add_argument(
        "--max-label-len",
        type=int,
        default=20,
        help="Max characters for labels (default: 20)",
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
        show_guards=args.show_guards,
        show_counts=args.show_counts,
        cluster_activities=not args.no_cluster,
        max_label_len=args.max_label_len,
    )


if __name__ == "__main__":
    main()
