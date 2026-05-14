# Vigil

Self-evolving neuro-symbolic runtime verification for mobile GUI agents.

## Overview

Vigil is a safety layer for mobile GUI agents. It constructs per-app FSM+DSL verifier bundles offline, then checks every proposed GUI action online before execution and returns `ALLOW`, `DENY`, or `UNCERTAIN`.

The project is organized around three mobile GUI error families:

| Error Family | Vigil Mechanism |
|--------------|-----------------|
| GUI state and transition errors | FSM state localization, transition validity, reachability, loop detection. |
| GUI semantic binding errors | DSL guards, frozen intent variables, task-state tracking, parameterized templates. |
| GUI safety and side-effect errors | Safety guards, invariants, irreversible-action checks, runtime monitoring. |

## Documentation

- [CLAUDE.md](CLAUDE.md) and [AGENTS.md](AGENTS.md): full agent context, implementation conventions, and research positioning.
- [docs/architecture.md](docs/architecture.md): system architecture and runtime decision flow.
- [docs/error_taxonomy.md](docs/error_taxonomy.md): three-level error taxonomy, module mapping, and benchmark alignment.
- [docs/nsdi_paper_outline.md](docs/nsdi_paper_outline.md): compact NSDI-style paper outline.
- [docs/dsl_grammar.lark](docs/dsl_grammar.lark): formal grammar for semantic guards.

## Quick Start

```bash
uv sync --group dev
uv run pytest tests/
```

Use `uv` for all package and command execution in this repository.
