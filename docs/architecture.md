# Vigil Architecture

Vigil is a runtime verification layer for mobile GUI agents. It does not replace the agent. It wraps the agent and checks each proposed GUI action before execution.

## Design Goal

Given a current screen, proposed action, and frozen user intent, Vigil must return exactly one verdict:

```text
ALLOW      The action is valid, semantically consistent, and safe enough to execute.
DENY       The action violates topology, semantics, or safety constraints.
UNCERTAIN  The verifier cannot prove safety with sufficient confidence.
```

## System Flow

```text
Offline construction
  Android app exploration
  -> UI tree parsing and action enumeration
  -> state abstraction and structural fingerprints
  -> hierarchical FSM construction
  -> DSL guard generation
  -> replay verification and confidence scoring
  -> FSM+DSL bundle

Online verification
  current screen + proposed action + frozen intent
  -> state localization
  -> FSM transition and reachability checks
  -> DSL guard evaluation
  -> safety invariant checks
  -> ALLOW / DENY / UNCERTAIN
```

## Core Components

| Layer | Modules | Responsibility |
|-------|---------|----------------|
| Models | `models/fsm.py`, `models/state.py`, `models/action.py`, `models/dsl.py` | Typed representation of states, transitions, actions, guards, and bundles. |
| Core parsing | `core/ui_parser.py`, `core/action_types.py`, `core/platform_priors.py` | Convert Android UI trees into structured screens and candidate actions. |
| Offline neuro layer | `neuro/explorer.py`, `neuro/state_abstractor.py`, `neuro/fsm_builder.py`, `neuro/dsl_generator.py`, `neuro/replay_verifier.py`, `neuro/evolution.py` | Build and validate per-app FSM+DSL artifacts. |
| Online symbolic layer | `symbolic/state_locator.py`, `symbolic/fsm_checker.py`, `symbolic/dsl_evaluator.py`, `symbolic/invariant_checker.py`, `symbolic/trajectory_verifier.py`, `symbolic/decision_engine.py` | Check each proposed action before execution. |
| Integration | `integration/agent_runner.py`, `scripts/verify_action.py` | Wrap external GUI agents and expose CLI/runtime entry points. |

## Current FSM Schema

`AbstractState` now uses nested canonical storage:

- `identity`: deterministic functional and structural hashes.
- `android_context`: Android activity/package/window observation context.
- `evidence`: trace-derived raw screen IDs and construction trust.
- `abstraction`: dynamic container and sub-FSM template metadata.
- `invariant_specs`: runtime-checkable invariants with confidence and provenance.
- `annotations`: LLM-derived, non-authoritative labels and widget aliases.
- `legacy_invariants`: non-runtime compatibility data; never merge this into `invariant_specs`.

New code should read and write nested paths such as `state.identity.functional_hash`, `state.evidence.raw_screen_ids`, `state.abstraction.container_type`, `state.invariant_specs`, and `state.annotations`. Flat names remain only as compatibility aliases for old kwargs and old FSM JSON.

`AppFSM.serialize()` writes `schema_version` 4 nested-only JSON. `AppFSM.deserialize()` accepts schema versions 2, 3, and 4 so historical flat bundles still load, but new writers must not reintroduce top-level flat state mirrors.

## Taxonomy Alignment

| Runtime Question | Error Family | Main Check |
|------------------|--------------|------------|
| Can the agent legally move here? | GUI State and Transition Errors | State localization, transition validity, reachability, confidence. |
| Is the agent doing the right thing here? | GUI Semantic Binding Errors | Intent extraction, DSL guards, task progress, parameterized templates. |
| Is this action safe to commit? | GUI Safety and Side-Effect Errors | Frozen intent, invariants, irreversible-action checks, safety guards. |

## Three-Tier Fallback Strategy

The fallback tiers are about coverage, not error categories.

| Tier | Trigger | Behavior |
|------|---------|----------|
| Tier 1: Structural FSM verification | Current state is known and action is represented in the FSM. | Pure symbolic topology checks. |
| Tier 2: Parameterized guard verification | Action is structurally legal but requires runtime semantic binding. | Bind `$intent.*` variables and evaluate DSL predicates. |
| Tier 3: Micro-evolution | Current state is unknown or dynamic content does not match a known template. | Try structural inheritance first; otherwise generate a candidate state/guard asynchronously and gate trust through replay confidence. |

## Trust Boundary

Vigil may use LLMs during offline construction and asynchronous Tier 3 evolution. The common online path must remain symbolic and deterministic. New evolved states increase coverage immediately, but they do not become high-trust until replay verification raises their confidence.
