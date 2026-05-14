# NSDI Paper Outline

This outline keeps the paper compact while making the three mobile GUI error families visible from motivation through evaluation.

## Main Sections

```latex
\section{Introduction}
\section{Motivation and Design Goals}
\section{Vigil Overview}
\section{Offline Model Construction}
\section{Online Runtime Verification}
\section{Implementation}
\section{Evaluation}
\section{Discussion, Related Work, and Conclusion}
```

## Section Spine

### 1. Introduction

Purpose: motivate why mobile GUI agents need deterministic action-level verification.

Must include:

- Mobile agents operate real devices with real side effects.
- Existing verification strategies are either probabilistic, expensive, or manually specified.
- The key gap: automatically constructed per-app verifier, deterministic at runtime, cheap enough for on-device use.
- One paragraph naming the three GUI error families.
- Summary of Vigil's offline neuro construction and online symbolic verification.

### 2. Motivation and Design Goals

Purpose: define the failure space and derive requirements.

Suggested subsections:

- Mobile GUI agent failure taxonomy.
- Why LLM-as-a-Judge, auxiliary neural verifiers, and hand-authored logic fall short.
- Design requirements: black-box GUI operation, per-action runtime checks, per-app adaptation, low latency, deterministic verdicts.

### 3. Vigil Overview

Purpose: show the whole system before deep dives.

Suggested subsections:

- Threat model and assumptions.
- Neuro-symbolic architecture.
- Offline construction versus online verification.
- Mapping from error families to Vigil mechanisms.

### 4. Offline Model Construction

Purpose: explain how Vigil constructs the verifier artifact.

Suggested subsections:

- UI exploration and action enumeration.
- State abstraction and structural fingerprinting.
- Hierarchical FSM construction.
- DSL guard generation.
- Replay-based FSM verification and confidence scoring.

### 5. Online Runtime Verification

Purpose: explain how Vigil checks every proposed action.

Suggested subsections:

- State and transition verification.
- Semantic binding verification.
- Safety and side-effect verification.
- Uncertainty handling and micro-evolution.
- Final decision engine: `ALLOW`, `DENY`, `UNCERTAIN`.

### 6. Implementation

Purpose: make the system credible and reproducible.

Suggested content:

- Android Accessibility/uiautomator2 integration.
- FSM and DSL runtime implementation.
- Agent wrapper integration.
- Bundle storage and evolution cache.
- On-device latency optimizations.

### 7. Evaluation

Purpose: prove the verifier blocks the taxonomy, not just improves task success.

Suggested research questions:

- RQ1: How effectively does Vigil reduce unsafe or incorrect GUI actions overall?
- RQ2: How much does each verification layer reduce its corresponding error family?
- RQ3: What is the runtime latency and device overhead per action?
- RQ4: How well does micro-evolution improve coverage on dynamic apps?
- RQ5: What happens when each component is ablated?

Suggested subsection order:

- Experimental setup.
- Overall effectiveness.
- Per-error-family analysis.
- Latency and resource cost.
- Micro-evolution and dynamic-content coverage.
- Ablation study.

### 8. Discussion, Related Work, and Conclusion

Purpose: close the argument without adding too much section overhead.

Suggested content:

- Limits: WebView, inaccessible widgets, app updates, LLM-generated guard quality.
- Broader applicability beyond Android GUI.
- Related work clusters: mobile-agent benchmarks, GUI model-based testing, UI grounding, runtime safety/formal guardrails.
- Final statement: Vigil is a safety layer for GUI agents, not a replacement agent.

## Writing Rule

Every technical section should answer one of these questions:

1. Can the agent legally move here?
2. Is the agent doing the right thing here?
3. Is this action safe to commit?
