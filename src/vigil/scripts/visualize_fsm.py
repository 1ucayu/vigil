"""CLI entry point: vigil-visualize.

Visualizes a constructed FSM as a graph image using Graphviz.

Usage:
    vigil-visualize --fsm <fsm.json>
    vigil-visualize --fsm <fsm.json> --format html
    vigil-visualize --fsm <fsm.json> --output <output.png>
    vigil-visualize --fsm <fsm.json> --output <output.svg> --format svg

When --output is omitted, generated files are written under output_docs/.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from loguru import logger

from vigil.core.paths import OUTPUT_DOCS_DIR, resolve_generated_output_path

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

_SAFE_STATE_FIELDS = (
    "state_id",
    "name",
    "hierarchy_level",
    "parent_state",
    "activity_name",
    "container_type",
    "sub_fsm_template_id",
)


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

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_stem = str(output_path).removesuffix(f".{fmt}")
    dot.render(out_stem, cleanup=True)
    logger.info(f"FSM graph rendered to {output_path}")


def default_output_path(fsm_path: Path, fmt: str) -> Path:
    """Default generated visualization path under output_docs/."""
    app_slug = _infer_app_slug(fsm_path)
    if fmt == "html":
        return OUTPUT_DOCS_DIR / app_slug / "fsm.html"
    return OUTPUT_DOCS_DIR / f"{app_slug}_fsm.{fmt}"


def _infer_app_slug(fsm_path: Path) -> str:
    try:
        payload = json.loads(fsm_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}

    app_package = payload.get("app_package") if isinstance(payload, dict) else None
    if isinstance(app_package, str) and app_package:
        return app_package.replace(".", "_")
    if fsm_path.stem == "fsm" and fsm_path.parent.name:
        return fsm_path.parent.name
    return fsm_path.stem or "fsm"


def _fsm_to_view_dict(
    fsm: Any,
    show_guards: bool = False,
    include_sensitive_details: bool = False,
    screens_dir: Path | None = None,
) -> dict[str, Any]:
    """Convert an AppFSM to a frontend-friendly, JSON-serializable dict."""
    activities = sorted({s.activity_name or "" for s in fsm.states.values()})
    activity_colors = {
        act: _ACTIVITY_COLORS[i % len(_ACTIVITY_COLORS)] for i, act in enumerate(activities)
    }

    states = [
        _state_to_view_dict(
            state,
            include_sensitive_details=include_sensitive_details,
            screens_dir=screens_dir,
        )
        for state in fsm.states.values()
    ]
    transitions = [
        _transition_to_view_dict(
            transition,
            show_guards=show_guards,
            include_sensitive_details=include_sensitive_details,
        )
        for transition in fsm.transitions
    ]

    return {
        "app_package": fsm.app_package,
        "version": fsm.version,
        "initial_state": fsm.initial_state,
        "summary": {
            "num_states": len(fsm.states),
            "num_transitions": len(fsm.transitions),
        },
        "action_colors": dict(_ACTION_COLORS),
        "activity_colors": activity_colors,
        "initial_color": _INITIAL_COLOR,
        "initial_font_color": _INITIAL_FONT_COLOR,
        "states": states,
        "transitions": transitions,
    }


def _state_to_view_dict(
    state: Any,
    include_sensitive_details: bool,
    screens_dir: Path | None = None,
) -> dict[str, Any]:
    """Convert a state to the HTML view schema."""
    state_dict = state.model_dump(mode="json")
    if include_sensitive_details:
        if screens_dir is not None:
            images = _load_screen_images(state_dict.get("raw_screens", []), screens_dir)
            if images:
                state_dict["raw_screen_images"] = images
        return state_dict
    return {field: state_dict.get(field) for field in _SAFE_STATE_FIELDS}


def _load_screen_images(screen_ids: list[str], screens_dir: Path) -> list[dict[str, str]]:
    """Load raw screenshot files as base64 data URIs.

    Looks up '<screens_dir>/<screen_id>.png' (or .jpg/.jpeg/.webp) per id.
    Missing files are silently skipped — log at debug level.
    """
    images: list[dict[str, str]] = []
    extensions = (".png", ".jpg", ".jpeg", ".webp")
    for sid in screen_ids:
        path: Path | None = None
        for ext in extensions:
            candidate = screens_dir / f"{sid}{ext}"
            if candidate.exists():
                path = candidate
                break
        if path is None:
            logger.debug(f"screenshot not found for screen_id={sid} in {screens_dir}")
            continue
        try:
            raw = path.read_bytes()
        except OSError as err:
            logger.debug(f"failed to read screenshot {path}: {err}")
            continue
        mime, _ = mimetypes.guess_type(path.name)
        mime = mime or "image/png"
        data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        images.append({"screen_id": sid, "data_uri": data_uri})
    return images


def _transition_to_view_dict(
    transition: Any,
    show_guards: bool,
    include_sensitive_details: bool,
) -> dict[str, Any]:
    """Convert a transition to the HTML view schema."""
    if include_sensitive_details:
        transition_dict = transition.model_dump(mode="json")
        if not show_guards:
            transition_dict.pop("guard", None)
        return transition_dict

    view = {
        "source": transition.source,
        "target": transition.target,
        "action": {"type": transition.action.get("type")},
        "confidence": transition.confidence,
        "observed_count": transition.observed_count,
    }
    if show_guards and transition.guard:
        view["guard"] = transition.guard
    return view


def render_fsm_html(
    fsm_path: Path,
    output_path: Path,
    show_guards: bool = False,
    show_counts: bool = False,
    max_label_len: int = 20,
    include_sensitive_details: bool = False,
    screens_dir: Path | None = None,
) -> None:
    """Render an FSM as a self-contained interactive HTML file."""
    from vigil.models.fsm import AppFSM

    fsm = AppFSM.deserialize(fsm_path)
    logger.info(f"Loaded {fsm}")

    if screens_dir is not None and not include_sensitive_details:
        logger.warning(
            "--screens-dir was provided but --include-sensitive-details is not set; "
            "screenshots are sensitive and will NOT be embedded."
        )
        screens_dir = None

    view = _fsm_to_view_dict(
        fsm,
        show_guards=show_guards,
        include_sensitive_details=include_sensitive_details,
        screens_dir=screens_dir,
    )
    view["options"] = {
        "show_guards": show_guards,
        "show_counts": show_counts,
        "max_label_len": max_label_len,
        "include_sensitive_details": include_sensitive_details,
    }

    payload = json.dumps(view, ensure_ascii=False)
    # Avoid prematurely closing the <script> if any field contains '</script>'.
    payload = payload.replace("</", "<\\/")

    html = _HTML_TEMPLATE.replace("__FSM_DATA__", payload)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info(f"Interactive FSM HTML written to {output_path}")


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Vigil FSM Viewer</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    height: 100%;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: #222;
    background: #fafafa;
  }
  #app { display: flex; flex-direction: column; height: 100vh; }
  header {
    padding: 10px 16px;
    background: #1565c0;
    color: white;
    display: flex;
    gap: 18px;
    flex-wrap: wrap;
    align-items: baseline;
  }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; }
  header .meta { font-size: 12px; opacity: 0.9; }
  header .meta b { font-weight: 600; }
  main { flex: 1; display: flex; min-height: 0; }
  #canvas-wrap {
    flex: 1;
    position: relative;
    background: #ffffff;
    overflow: hidden;
    border-right: 1px solid #e0e0e0;
  }
  svg#canvas { width: 100%; height: 100%; cursor: grab; display: block; }
  svg#canvas.panning { cursor: grabbing; }
  #sidebar {
    width: 380px;
    max-width: 40vw;
    overflow-y: auto;
    padding: 14px 16px;
    background: #ffffff;
  }
  #sidebar h2 { font-size: 14px; margin: 0 0 8px; color: #1565c0; }
  #sidebar .empty { color: #888; padding: 24px 0; text-align: center; font-style: italic; }
  #sidebar .field { margin: 6px 0; display: flex; gap: 8px; align-items: baseline; }
  #sidebar .field .k {
    color: #666;
    font-size: 11px;
    min-width: 130px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }
  #sidebar .field .v {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    word-break: break-all;
    font-size: 12px;
    flex: 1;
  }
  #sidebar .section { margin-top: 14px; padding-top: 10px; border-top: 1px solid #eee; }
  #sidebar .section h3 {
    font-size: 12px;
    margin: 0 0 6px;
    color: #333;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  #sidebar ul { margin: 0; padding-left: 18px; }
  #sidebar li {
    margin: 2px 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    word-break: break-all;
  }
  #sidebar .tr {
    padding: 6px 8px;
    margin: 4px 0;
    background: #f5f7fa;
    border-left: 3px solid #ccc;
    border-radius: 3px;
    font-size: 12px;
  }
  #sidebar .tr .arrow { color: #1565c0; font-weight: 600; }
  #sidebar .tr .row {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    word-break: break-all;
  }
  #sidebar .muted { color: #999; }
  #sidebar .screens { display: flex; flex-direction: column; gap: 10px; }
  #sidebar .screen {
    border: 1px solid #ddd;
    border-radius: 4px;
    padding: 6px;
    background: #fafafa;
  }
  #sidebar .screen .sid {
    font-size: 11px;
    color: #666;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    margin-bottom: 4px;
    word-break: break-all;
  }
  #sidebar .screen img {
    width: 100%;
    height: auto;
    display: block;
    border-radius: 2px;
    cursor: zoom-in;
    background: #000;
  }
  #lightbox {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.85);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    cursor: zoom-out;
  }
  #lightbox.open { display: flex; }
  #lightbox img { max-width: 95vw; max-height: 95vh; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }
  .node circle { stroke: #333; stroke-width: 1.2; cursor: pointer; transition: stroke-width 0.1s; }
  .node.initial circle { stroke-width: 3; stroke: #0d3a6b; }
  .node.selected circle { stroke: #ff6f00; stroke-width: 3.5; }
  .node text {
    pointer-events: none;
    font-size: 10px;
    fill: #222;
    text-anchor: middle;
    user-select: none;
  }
  .edge path { fill: none; stroke-width: 1.3; }
  .edge text { font-size: 9px; fill: #444; pointer-events: none; user-select: none; }
  footer {
    padding: 4px 12px;
    font-size: 11px;
    color: #888;
    border-top: 1px solid #eee;
    background: #fafafa;
  }
</style>
</head>
<body>
<div id="app">
  <header>
    <h1 id="app-title">Vigil FSM</h1>
    <div class="meta">version <b id="m-version"></b></div>
    <div class="meta"><b id="m-states"></b> states</div>
    <div class="meta"><b id="m-transitions"></b> transitions</div>
    <div class="meta">initial: <b id="m-initial"></b></div>
  </header>
  <main>
    <div id="canvas-wrap">
      <svg id="canvas">
        <defs id="defs"></defs>
        <g id="viewport">
          <g id="edges"></g>
          <g id="nodes"></g>
        </g>
      </svg>
    </div>
    <aside id="sidebar">
      <div id="sidebar-content">
        <div class="empty">Click a state to view details.</div>
      </div>
    </aside>
  </main>
  <footer>Drag nodes &middot; drag empty space to pan &middot; scroll to zoom</footer>
</div>
<div id="lightbox"><img alt=""></div>

<script>
const FSM_DATA = __FSM_DATA__;

(function() {
  const opts = FSM_DATA.options || {};
  const MAX_LABEL = opts.max_label_len || 20;
  const SHOW_GUARDS = !!opts.show_guards;
  const SHOW_COUNTS = !!opts.show_counts;
  const INCLUDE_SENSITIVE_DETAILS = !!opts.include_sensitive_details;

  // Header
  document.getElementById('app-title').textContent = FSM_DATA.app_package || 'Vigil FSM';
  document.getElementById('m-version').textContent = FSM_DATA.version || '';
  document.getElementById('m-states').textContent = FSM_DATA.summary.num_states;
  document.getElementById('m-transitions').textContent = FSM_DATA.summary.num_transitions;
  document.getElementById('m-initial').textContent = FSM_DATA.initial_state || '(none)';

  const statesById = {};
  FSM_DATA.states.forEach(s => { statesById[s.state_id] = s; });

  // Index transitions for sidebar
  const outgoing = {}, incoming = {};
  FSM_DATA.transitions.forEach(t => {
    (outgoing[t.source] = outgoing[t.source] || []).push(t);
    (incoming[t.target] = incoming[t.target] || []).push(t);
  });

  // ---- Layout (simple force-directed) ----
  const W = 1200, H = 800;
  const nodes = FSM_DATA.states.map((s, i) => {
    const point = initialPosition(i, FSM_DATA.states.length);
    return {
      id: s.state_id,
      x: point.x,
      y: point.y,
      vx: 0, vy: 0,
      data: s,
    };
  });
  const nodeIndex = {};
  nodes.forEach(n => { nodeIndex[n.id] = n; });
  const links = FSM_DATA.transitions
    .map(t => ({ source: nodeIndex[t.source], target: nodeIndex[t.target], data: t }))
    .filter(l => l.source && l.target);

  function initialPosition(i, total) {
    if (total > 100) {
      const cols = Math.ceil(Math.sqrt(total));
      const spacing = 72;
      const row = Math.floor(i / cols);
      const col = i % cols;
      return {
        x: W / 2 + (col - (cols - 1) / 2) * spacing,
        y: H / 2 + (row - Math.floor(total / cols) / 2) * spacing,
      };
    }
    const angle = (i / Math.max(1, total)) * Math.PI * 2;
    const radius = Math.max(180, Math.min(340, total * 8));
    return {
      x: W / 2 + Math.cos(angle) * radius,
      y: H / 2 + Math.sin(angle) * radius,
    };
  }

  const edgeGroups = {};
  links.forEach(l => {
    const key = l.source.id + '\u2192' + l.target.id;
    (edgeGroups[key] = edgeGroups[key] || []).push(l);
  });
  Object.values(edgeGroups).forEach(group => {
    group.forEach((l, i) => {
      l.parallelIndex = i;
      l.parallelCount = group.length;
      l.parallelOffset = (i - (group.length - 1) / 2) * 28;
    });
  });

  const totalIterations = Math.max(50, Math.min(300, 5000 / nodes.length));
  const simulationChunkSize = 20;
  let completedIterations = 0;

  function simulateStep(iterations) {
    const k = 80;          // ideal spring length
    const rep = 9000;      // repulsion
    const springK = 0.05;
    const damping = 0.85;
    for (let iter = 0; iter < iterations; iter++) {
      // repulsion
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy + 0.01;
          let d = Math.sqrt(d2);
          let f = rep / d2;
          let fx = (dx / d) * f, fy = (dy / d) * f;
          a.vx += fx; a.vy += fy;
          b.vx -= fx; b.vy -= fy;
        }
      }
      // springs
      links.forEach(l => {
        const dx = l.target.x - l.source.x, dy = l.target.y - l.source.y;
        const d = Math.sqrt(dx * dx + dy * dy) + 0.01;
        const f = (d - k) * springK;
        const fx = (dx / d) * f, fy = (dy / d) * f;
        l.source.vx += fx; l.source.vy += fy;
        l.target.vx -= fx; l.target.vy -= fy;
      });
      // center pull
      nodes.forEach(n => {
        n.vx += (W / 2 - n.x) * 0.002;
        n.vy += (H / 2 - n.y) * 0.002;
      });
      // integrate
      nodes.forEach(n => {
        n.vx *= damping; n.vy *= damping;
        n.x += n.vx; n.y += n.vy;
      });
    }
  }

  function runSimulationChunk() {
    if (completedIterations >= totalIterations) return;
    const nextIterations = Math.min(simulationChunkSize, totalIterations - completedIterations);
    simulateStep(nextIterations);
    completedIterations += nextIterations;
    updatePositions();
    window.requestAnimationFrame(runSimulationChunk);
  }

  // ---- Render SVG ----
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const svg = document.getElementById('canvas');
  const viewport = document.getElementById('viewport');
  const edgesG = document.getElementById('edges');
  const nodesG = document.getElementById('nodes');
  const defs = document.getElementById('defs');

  // Build arrow markers per action color
  const allColors = new Set(Object.values(FSM_DATA.action_colors));
  allColors.add('#888888');
  allColors.forEach(c => {
    const marker = document.createElementNS(SVG_NS, 'marker');
    marker.setAttribute('id', 'arrow-' + colorId(c));
    marker.setAttribute('viewBox', '0 0 10 10');
    marker.setAttribute('refX', '9');
    marker.setAttribute('refY', '5');
    marker.setAttribute('markerWidth', '7');
    marker.setAttribute('markerHeight', '7');
    marker.setAttribute('orient', 'auto-start-reverse');
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
    path.setAttribute('fill', c);
    marker.appendChild(path);
    defs.appendChild(marker);
  });

  function colorId(c) { return c.replace('#', ''); }
  function truncate(s, n) { return (s && s.length > n) ? s.slice(0, n - 1) + '…' : (s || ''); }

  function edgeLabel(t) {
    const at = (t.action && t.action.type) || '?';
    const isBack = (at === 'navigate_back' || at === 'navigate_home');
    const parts = [isBack ? 'back' : at];
    if (SHOW_COUNTS && t.observed_count > 1) parts.push('×' + t.observed_count);
    if (SHOW_GUARDS && t.guard) parts.push('[' + truncate(t.guard, 30) + ']');
    return parts.join(' ');
  }

  function nodeFill(s) {
    if (s.state_id === FSM_DATA.initial_state) return FSM_DATA.initial_color;
    return FSM_DATA.activity_colors[s.activity_name || ''] || '#e8e8e8';
  }

  function nodeRadius(s) {
    const base = 22;
    const extra = Math.min(10, (s.name || s.state_id).length * 0.3);
    return base + extra;
  }

  // Edges
  const linkEls = links.map(l => {
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', 'edge');
    const path = document.createElementNS(SVG_NS, 'path');
    const at = (l.data.action && l.data.action.type) || '?';
    const color = FSM_DATA.action_colors[at] || '#888888';
    const isBack = (at === 'navigate_back' || at === 'navigate_home');
    path.setAttribute('stroke', color);
    if (isBack) path.setAttribute('stroke-dasharray', '4,3');
    path.setAttribute('marker-end', 'url(#arrow-' + colorId(color) + ')');
    g.appendChild(path);
    const label = document.createElementNS(SVG_NS, 'text');
    label.setAttribute('fill', color);
    label.textContent = edgeLabel(l.data);
    g.appendChild(label);
    edgesG.appendChild(g);
    l._path = path; l._label = label;
    return l;
  });

  // Nodes
  let selectedId = null;
  const nodeEls = nodes.map(n => {
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', 'node' + (n.id === FSM_DATA.initial_state ? ' initial' : ''));
    const circle = document.createElementNS(SVG_NS, 'circle');
    circle.setAttribute('r', nodeRadius(n.data));
    circle.setAttribute('fill', nodeFill(n.data));
    const text = document.createElementNS(SVG_NS, 'text');
    text.setAttribute('dy', '0.35em');
    if (n.id === FSM_DATA.initial_state) text.setAttribute('fill', FSM_DATA.initial_font_color);
    text.textContent = truncate(n.data.name || n.id, MAX_LABEL);
    g.appendChild(circle);
    g.appendChild(text);
    nodesG.appendChild(g);
    n._g = g; n._circle = circle; n._text = text;

    g.addEventListener('click', (ev) => {
      ev.stopPropagation();
      selectState(n.id);
    });
    attachDrag(n);
    return n;
  });

  function updatePositions() {
    nodeEls.forEach(n => {
      n._g.setAttribute('transform', 'translate(' + n.x + ',' + n.y + ')');
    });
    linkEls.forEach(l => {
      if (l.source === l.target) {
        const r = nodeRadius(l.source.data);
        const loopLift = r + 34 + (l.parallelIndex || 0) * 16;
        const sx = l.source.x + r * 0.65;
        const sy = l.source.y - r * 0.65;
        const tx = l.source.x - r * 0.65;
        const ty = l.source.y - r * 0.65;
        const c1x = l.source.x + loopLift;
        const c1y = l.source.y - loopLift;
        const c2x = l.source.x - loopLift;
        const c2y = l.source.y - loopLift;
        const loopPath = 'M ' + sx + ' ' + sy
          + ' C ' + c1x + ' ' + c1y
          + ' ' + c2x + ' ' + c2y
          + ' ' + tx + ' ' + ty;
        l._path.setAttribute('d', loopPath);
        l._label.setAttribute('x', l.source.x);
        l._label.setAttribute('y', l.source.y - loopLift - 4);
        return;
      }
      const dx = l.target.x - l.source.x, dy = l.target.y - l.source.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const sr = nodeRadius(l.source.data), tr = nodeRadius(l.target.data) + 6;
      const sx = l.source.x + (dx / d) * sr;
      const sy = l.source.y + (dy / d) * sr;
      const tx = l.target.x - (dx / d) * tr;
      const ty = l.target.y - (dy / d) * tr;
      const offset = l.parallelOffset || 0;
      if (offset === 0) {
        l._path.setAttribute('d', 'M ' + sx + ' ' + sy + ' L ' + tx + ' ' + ty);
        l._label.setAttribute('x', (sx + tx) / 2);
        l._label.setAttribute('y', (sy + ty) / 2 - 3);
        return;
      }
      const nx = -dy / d, ny = dx / d;
      const mx = (sx + tx) / 2 + nx * offset;
      const my = (sy + ty) / 2 + ny * offset;
      l._path.setAttribute('d', 'M ' + sx + ' ' + sy + ' Q ' + mx + ' ' + my + ' ' + tx + ' ' + ty);
      l._label.setAttribute('x', mx);
      l._label.setAttribute('y', my - 3);
    });
  }
  updatePositions();
  window.requestAnimationFrame(runSimulationChunk);

  // ---- Pan / zoom ----
  let tx = 0, ty = 0, scale = 1;
  function applyTransform() {
    viewport.setAttribute('transform', 'translate(' + tx + ',' + ty + ') scale(' + scale + ')');
  }
  // initial center
  const rect0 = svg.getBoundingClientRect();
  tx = rect0.width / 2 - W / 2;
  ty = rect0.height / 2 - H / 2;
  applyTransform();

  let isPanning = false, panStart = null;
  svg.addEventListener('mousedown', (ev) => {
    if (ev.target.closest('.node')) return;
    isPanning = true;
    panStart = { x: ev.clientX - tx, y: ev.clientY - ty };
    svg.classList.add('panning');
  });
  window.addEventListener('mousemove', (ev) => {
    if (!isPanning) return;
    tx = ev.clientX - panStart.x;
    ty = ev.clientY - panStart.y;
    applyTransform();
  });
  window.addEventListener('mouseup', () => { isPanning = false; svg.classList.remove('panning'); });

  svg.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const rect = svg.getBoundingClientRect();
    const cx = ev.clientX - rect.left;
    const cy = ev.clientY - rect.top;
    const factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
    const newScale = Math.max(0.1, Math.min(4, scale * factor));
    // zoom around cursor
    tx = cx - (cx - tx) * (newScale / scale);
    ty = cy - (cy - ty) * (newScale / scale);
    scale = newScale;
    applyTransform();
  }, { passive: false });

  svg.addEventListener('click', (ev) => {
    if (!ev.target.closest('.node')) {
      selectState(null);
    }
  });

  // ---- Node drag ----
  function attachDrag(n) {
    let dragging = false, offset = null;
    n._g.addEventListener('mousedown', (ev) => {
      ev.stopPropagation();
      dragging = true;
      const pt = svgPoint(ev.clientX, ev.clientY);
      offset = { x: pt.x - n.x, y: pt.y - n.y };
    });
    window.addEventListener('mousemove', (ev) => {
      if (!dragging) return;
      const pt = svgPoint(ev.clientX, ev.clientY);
      n.x = pt.x - offset.x;
      n.y = pt.y - offset.y;
      updatePositions();
    });
    window.addEventListener('mouseup', () => { dragging = false; });
  }
  function svgPoint(clientX, clientY) {
    const rect = svg.getBoundingClientRect();
    return { x: (clientX - rect.left - tx) / scale, y: (clientY - rect.top - ty) / scale };
  }

  // ---- Sidebar ----
  function selectState(id) {
    selectedId = id;
    nodeEls.forEach(n => {
      n._g.classList.toggle('selected', n.id === id);
    });
    renderSidebar();
  }

  const sidebar = document.getElementById('sidebar-content');
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function field(k, v) {
    const wrap = el('div', 'field');
    wrap.appendChild(el('div', 'k', k));
    const val = el('div', 'v');
    if (v === null || v === undefined || v === '') {
      val.classList.add('muted');
      val.textContent = '—';
    } else if (typeof v === 'object') {
      val.textContent = JSON.stringify(v);
    } else {
      val.textContent = String(v);
    }
    wrap.appendChild(val);
    return wrap;
  }
  function section(title) {
    const s = el('div', 'section');
    s.appendChild(el('h3', null, title));
    return s;
  }
  function listFrom(arr) {
    const ul = el('ul');
    if (!arr || arr.length === 0) {
      const li = el('li', 'muted', '(none)');
      ul.appendChild(li);
      return ul;
    }
    arr.forEach(x => ul.appendChild(el('li', null, typeof x === 'object' ? JSON.stringify(x) : x)));
    return ul;
  }

  function appendDetail(parent, key, value) {
    if (key === 'raw_screen_images' && Array.isArray(value)) {
      if (value.length === 0) return;
      const sec = section('raw screenshots');
      const wrap = el('div', 'screens');
      value.forEach(item => {
        const card = el('div', 'screen');
        card.appendChild(el('div', 'sid', item.screen_id || ''));
        const img = document.createElement('img');
        img.alt = item.screen_id || 'screenshot';
        img.src = item.data_uri || '';
        img.addEventListener('click', () => openLightbox(img.src, img.alt));
        card.appendChild(img);
        wrap.appendChild(card);
      });
      sec.appendChild(wrap);
      parent.appendChild(sec);
      return;
    }
    if (Array.isArray(value)) {
      const sec = section(key);
      sec.appendChild(listFrom(value));
      parent.appendChild(sec);
      return;
    }
    if (value && typeof value === 'object') {
      const sec = section(key);
      const entries = Object.entries(value);
      if (entries.length === 0) {
        sec.appendChild(el('div', 'muted', '(none)'));
      } else {
        entries.forEach(([childKey, childValue]) => appendDetail(sec, childKey, childValue));
      }
      parent.appendChild(sec);
      return;
    }
    parent.appendChild(field(key, value));
  }

  const lightbox = document.getElementById('lightbox');
  const lightboxImg = lightbox.querySelector('img');
  function openLightbox(src, alt) {
    lightboxImg.src = src;
    lightboxImg.alt = alt || '';
    lightbox.classList.add('open');
  }
  lightbox.addEventListener('click', () => {
    lightbox.classList.remove('open');
    lightboxImg.src = '';
  });

  function renderTransition(t, direction) {
    const wrap = el('div', 'tr');
    const head = el('div', 'row');
    const peer = direction === 'out' ? t.target : t.source;
    const arrow = el('span', 'arrow', direction === 'out' ? ' → ' : ' ← ');
    head.appendChild(document.createTextNode(direction === 'out' ? 'to' : 'from'));
    head.appendChild(arrow);
    head.appendChild(document.createTextNode(peer));
    wrap.appendChild(head);
    const at = (t.action && t.action.type) || '?';
    wrap.appendChild(el('div', 'row', 'action: ' + at));
    if (INCLUDE_SENSITIVE_DETAILS && t.action && Object.keys(t.action).length > 1) {
      wrap.appendChild(el('div', 'row', 'action details: ' + JSON.stringify(t.action)));
    }
    if (SHOW_GUARDS && t.guard) wrap.appendChild(el('div', 'row', 'guard: ' + t.guard));
    const transitionMeta = 'confidence: ' + (t.confidence ?? 0)
      + '  observed: ' + (t.observed_count ?? 0);
    wrap.appendChild(el('div', 'row', transitionMeta));
    return wrap;
  }

  function renderSidebar() {
    clear(sidebar);
    if (!selectedId) {
      sidebar.appendChild(el('div', 'empty', 'Click a state to view details.'));
      return;
    }
    const s = statesById[selectedId];
    if (!s) return;
    sidebar.appendChild(el('h2', null, s.name || s.state_id));
    Object.entries(s).forEach(([key, value]) => appendDetail(sidebar, key, value));

    const outSec = section('outgoing transitions');
    const outs = outgoing[selectedId] || [];
    if (outs.length === 0) outSec.appendChild(el('div', 'muted', '(none)'));
    outs.forEach(t => outSec.appendChild(renderTransition(t, 'out')));
    sidebar.appendChild(outSec);

    const inSec = section('incoming transitions');
    const ins = incoming[selectedId] || [];
    if (ins.length === 0) inSec.appendChild(el('div', 'muted', '(none)'));
    ins.forEach(t => inSec.appendChild(renderTransition(t, 'in')));
    sidebar.appendChild(inSec);
  }

  renderSidebar();
})();
</script>
</body>
</html>
"""


def main() -> None:
    """Run the FSM visualization pipeline."""
    parser = argparse.ArgumentParser(
        prog="vigil-visualize",
        description="Visualize an FSM as a graph image.",
    )
    parser.add_argument("--fsm", required=True, help="Path to serialized FSM JSON file")
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output path. Defaults to output_docs/<app>_fsm.<format>, or "
            "output_docs/<app>/fsm.html for --format html."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["png", "svg", "pdf", "html"],
        default="png",
        help="Output format. 'html' produces a self-contained interactive viewer. (default: png)",
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
    parser.add_argument(
        "--include-sensitive-details",
        action="store_true",
        help="Include full state and transition details in HTML output",
    )
    parser.add_argument(
        "--screens-dir",
        type=str,
        default=None,
        help=(
            "Directory containing raw screenshot files named '<screen_id>.png' "
            "(or .jpg/.jpeg/.webp). When set together with --include-sensitive-details, "
            "screenshots are embedded as base64 in the HTML sidebar."
        ),
    )

    args = parser.parse_args()

    fsm_path = Path(args.fsm)
    if not fsm_path.exists():
        logger.error(f"FSM file not found: {fsm_path}")
        raise SystemExit(1)

    output_path = resolve_generated_output_path(
        args.output, default_output_path(fsm_path, args.format)
    )

    if args.format == "html":
        screens_dir: Path | None = None
        if args.screens_dir:
            screens_dir = Path(args.screens_dir)
            if not screens_dir.exists():
                logger.error(f"Screens directory not found: {screens_dir}")
                raise SystemExit(1)
        render_fsm_html(
            fsm_path=fsm_path,
            output_path=output_path,
            show_guards=args.show_guards,
            show_counts=args.show_counts,
            max_label_len=args.max_label_len,
            include_sensitive_details=args.include_sensitive_details,
            screens_dir=screens_dir,
        )
        return

    render_fsm(
        fsm_path=fsm_path,
        output_path=output_path,
        fmt=args.format,
        layout=args.layout,
        show_guards=args.show_guards,
        show_counts=args.show_counts,
        cluster_activities=not args.no_cluster,
        max_label_len=args.max_label_len,
    )


if __name__ == "__main__":
    main()
