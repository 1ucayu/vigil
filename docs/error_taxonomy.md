# Mobile GUI Agent Error Taxonomy

This document is the paper and implementation spine for Vigil. The taxonomy defines what can go wrong in mobile GUI agents; the runtime verifier defines how Vigil blocks each failure before the action is committed to the device.

## Taxonomy

| Error Family | Core Problem | Examples | Vigil Mechanism |
|--------------|--------------|----------|-----------------|
| GUI State and Transition Errors | The agent's believed app topology does not match the actual GUI state. | Wrong screen, illegal click, dead end, repeated loop, unreachable goal. | FSM state localization, transition validity, reachability, loop detection, replay confidence. |
| GUI Semantic Binding Errors | The agent reaches the right UI structure but binds the wrong runtime meaning. | Wrong amount, recipient, address, list item, option, field, or intent slot. | DSL guards, `$intent.*` variables, Task State Machine, parameterized templates. |
| GUI Safety and Side-Effect Errors | The action is structurally valid but unsafe to commit. | Unintended payment, message send, deletion, permission grant, privacy leak, prompt-injection response. | Safety guards, frozen intent, invariants, irreversible-action monitor, runtime decision engine. |

## Module Mapping

| Error Family | Offline Modules | Online Modules | Primary Verdicts |
|--------------|-----------------|----------------|------------------|
| GUI State and Transition Errors | `neuro/explorer.py`, `neuro/state_abstractor.py`, `neuro/fsm_builder.py`, `neuro/replay_verifier.py` | `symbolic/state_locator.py`, `symbolic/fsm_checker.py`, `symbolic/trajectory_verifier.py` | `DENY` for illegal transitions; `UNCERTAIN` for unknown or low-confidence states. |
| GUI Semantic Binding Errors | `neuro/semantic_grounder.py`, `neuro/dsl_generator.py`, `neuro/widget_templates.py`, `neuro/evolution.py` | `symbolic/intent_extractor.py`, `symbolic/dsl_evaluator.py`, `symbolic/trajectory_verifier.py`, `symbolic/decision_engine.py` | `DENY` for guard violations; `UNCERTAIN` for missing bindings. |
| GUI Safety and Side-Effect Errors | Guard generation, replay verification, invariant mining, side-effect/irreversible-action obligations | `symbolic/invariant_checker.py`, `symbolic/decision_engine.py`, `integration/agent_runner.py`, `scripts/verify_action.py` | `DENY` for invariant or safety violations; `UNCERTAIN` when side-effecting actions are under-specified. |

## Benchmark Alignment

Use benchmark results to justify the taxonomy and structure evaluation by family.

| Benchmark / Reference Cluster | Best-Supported Error Families | How To Use It In The Paper |
|-------------------------------|-------------------------------|-----------------------------|
| AndroidArena | State/transition, semantic binding, constrained execution | Motivates wrong-screen behavior, navigation complexity, cross-app tasks, and user constraints. |
| SPA-Bench | State/transition, semantic binding | Motivates UI interpretation, action grounding, memory retention, key-component matching, and long-horizon consistency. |
| AndroidWorld | State/transition, semantic binding | Provides dynamic Android tasks and executable state-based rewards for evaluating generalization beyond fixed traces. |
| MobileSafetyBench | Safety and side effects | Motivates helpfulness-vs-safety separation, harmful mobile actions, and indirect prompt injection. |
| Model-based Android testing: A3E, MobiGUITAR, DroidBot, Stoat, Sapienz | State/transition | Shows GUI state modeling and exploration are feasible, but historically used for testing/crash discovery rather than runtime verification. |
| UI grounding: RICO, Android in the Wild, Screen2Words, Widget Captioning, Ferret-UI, SeeClick, OmniParser | Semantic binding | Shows raw GUI structure is insufficient; agents need semantic grounding of widgets, values, and screen purpose. |
| Formal guardrails: VeriSafe Agent, AgentSpec, GuardAgent, VeriGuard | Safety and side effects | Positions Vigil against hand-authored or purely rule-based guardrails by emphasizing automatic per-app model construction. |

## Evaluation Metrics

| Error Family | Suggested Metrics |
|--------------|-------------------|
| GUI State and Transition Errors | Invalid-action reduction, wrong-state localization rate, dead-end reduction, loop reduction, reachability preservation, replay pass rate. |
| GUI Semantic Binding Errors | Wrong-field prevention, wrong-value prevention, intent-slot match rate, task-state progress accuracy, dynamic-template binding accuracy. |
| GUI Safety and Side-Effect Errors | Harm prevention rate, false-deny rate, unsafe-confirmation prevention, prompt-injection resistance, side-effect `UNCERTAIN` rate. |

## Design Rules

1. Treat the taxonomy as orthogonal to the three-tier runtime fallback strategy.
2. Prefer deterministic symbolic checks over prompts in the online path.
3. Freeze user intent before verification starts; do not let later GUI content rewrite it silently.
4. New evolved states increase coverage, not trust; replay confidence must gate promotion.
5. Every runtime path must end in exactly one verdict: `ALLOW`, `DENY`, or `UNCERTAIN`.
