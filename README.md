# Vigil

Self-Evolving Neuro-Symbolic Runtime Verification for Mobile GUI Agents.

## Overview

Vigil is a neuro-symbolic runtime verification system for mobile GUI agents. It constructs per-app hierarchical Finite State Machines (FSMs) offline using LLMs, then performs lightweight symbolic verification at runtime, falling back to an LLM only when the symbolic layer is uncertain.

## Quick Start

```bash
uv venv .venv --python 3.11
uv pip install -e ".[dev]"
```

See `CLAUDE.md` for full project documentation.
