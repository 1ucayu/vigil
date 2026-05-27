# CLAUDE.md - Vigil Working Context

This file is intentionally short so Claude Code starts quickly and focuses on the current engineering task. The full historical context was preserved before slimming:

- Full Claude context: `docs/context/CLAUDE.full.md`
- Full Codex/Agents context: `docs/context/AGENTS.full.md`
- Architecture notes: `docs/architecture.md`
- Error taxonomy: `docs/error_taxonomy.md`
- Paper outline: `docs/nsdi_paper_outline.md`
- DSL grammar: `output_docs/dsl_grammar.lark`
- Literature/design survey: `docs/references/neuro_symbolic_architecture_survey.md`

Read the full context only when the task needs paper positioning, complete research background, or detailed historical notes.

---

## Project Identity

| Field | Value |
|-------|-------|
| Title | Vigil: Self-Evolving Neuro-Symbolic Runtime Verification for Mobile GUI Agents |
| Author | Luca Yu |
| Email | lucayu@connect.hku.hk |
| Affiliation | The University of Hong Kong (HKU) |
| Repo path | `/Users/lucayu/Desktop/GitHub/vigil` |
| Target venue | NSDI 2026 style systems submission |

Vigil is a neuro-symbolic runtime verification system for mobile GUI agents. It builds a per-app, DSL-guarded, confidence-annotated EFSM offline from APK static priors plus exploration/replay traces, then checks proposed GUI actions online with symbolic verification in the common path.

---

## Current Priority

The current bottleneck is FSM construction and validation alignment, not UI exploration.

Focus on making the builder and validator agree on:

- `state_id`
- abstract-state templates
- selector semantics
- canonical action identity `<tau, q, v>`
- transition provenance
- replay confidence fields

Existing Settings traces are sufficient for the next development pass. Do not rerun the emulator just to collect more exploration data unless coverage is demonstrably missing, trace files are stale, or replay trials are needed to estimate `rho`.

Current verified snapshot after the AbstractState schema migration:

- New FSM JSON writes use `schema_version` 4, nested-only state payloads.
- Reader-side compatibility still accepts schema versions 2/3/4.
- Settings validation baseline: `total=385`, `ok=369`, `action_signature_mismatch=14`, `template_binding_missing=1`, `transition_not_in_fsm=1`.
- Fidelity replay baseline: `107/107 OK`.
- Full suite baseline: `654 passed`, `3 skipped`, `1 failed`; the remaining known failure is `tests/test_llm_client.py::TestProxyProvider::test_proxy_images_fallback`, unrelated to the FSM schema migration.

---

## Non-Negotiable Engineering Rules

- Use `uv` only. Do not use direct `pip`, and do not create `requirements.txt`.
- Run Python commands through `uv run`.
- Static APK files are priors, not proof of transitions.
- Trace/replay evidence is required before adding an edge to `delta`.
- The LLM may add semantic labels, risk annotations, DSL guard candidates, and provenance, but must not decide state equality, create static-only edges, assign replay confidence, or make runtime verdicts.
- XML/runtime traces remain the deterministic source of truth for fingerprinting, replay, selectors, and transition evidence.
- The compressed LLM view is only for prompting; never use it as the source of truth for localization or replay.
- Low-confidence or incomplete evidence should route to `UNCERTAIN`, not high-trust `ALLOW`.
- Keep implementation scoped to the requested task and follow existing code patterns.
- Do not revert unrelated user changes in the worktree.
- Generated visualizations, exported FSM viewers, and code-produced documentation artifacts belong under `output_docs/`, not `docs/`.

Recommended commands:

```bash
uv sync --group dev
uv run pytest tests/
uv run pytest tests/test_app_prior.py tests/test_fsm_builder.py tests/test_semantic_grounder.py tests/test_dsl_generator.py tests/test_replay_verifier.py
uv run vigil-explore --app com.android.settings --steps 20
```

Current FSM state schema:

- `AbstractState` canonical storage is nested: `identity`, `android_context`, `evidence`, `abstraction`, `invariant_specs`, `annotations`, and `legacy_invariants`.
- Use nested paths in new code, e.g. `state.identity.functional_hash`, `state.evidence.raw_screen_ids`, `state.abstraction.container_type`, `state.invariant_specs`, and `state.annotations`.
- Flat names such as `fingerprint`, `raw_screens`, `container_type`, `semantic_profile`, `state_invariants`, and `invariant_confidence` are compatibility aliases for old kwargs/JSON only.
- `AppFSM.serialize()` writes schema v4 nested-only JSON. Do not add new serialize-side flat mirrors.
- `legacy_invariants` is a non-runtime compatibility bag and must not be merged into `invariant_specs`.

FSM JSON schema versions:

- `schema_version` 2 = flat-only legacy.
- `schema_version` 3 = nested + flat mirrors transitional.
- `schema_version` 4 = nested-only canonical current.
- `AppFSM.deserialize()` accepts 2/3/4.

---

## Paper Model

Use this notation consistently in writing and implementation notes:

```text
M_A = <S, s0, Sigma, delta, Gamma, I, rho>
```

| Symbol | Meaning | Implementation Anchor |
|--------|---------|-----------------------|
| `S` | Abstract GUI states | `AppFSM.states`, `AbstractState` |
| `s0` | Initial app state | `AppFSM.initial_state` |
| `Sigma` | Canonical GUI action alphabet `<tau, q, v>` | `ActionType`, `Transition.action` |
| `delta` | Action-labeled transition relation | `AppFSM.transitions`, `networkx.DiGraph` edges |
| `Gamma` | Guard map from state/action pairs to DSL formulas | `Transition.guard`, `DSLEvaluator` |
| `I` | State/action/side-effect invariants | `AbstractState.invariant_specs`, `InvariantChecker` |
| `rho` | Replay confidence map | `Transition.confidence`, `FsmChecker` |

Describe `M_A` as a DSL-guarded, confidence-annotated EFSM built on the transition-system view underlying Kripke structures. Each verified transition may be read as:

```text
{ Gamma(s, a) } a { I(s', a) }
```

---

## Three Error Families

Vigil's narrative and tests should stay organized around:

1. GUI state and transition errors: wrong screen, illegal action, dead end, loop.
2. GUI semantic binding errors: wrong field, value, item, contact, address, or intent slot.
3. GUI safety and side-effect errors: structurally legal actions that violate user constraints or cause harmful irreversible effects.

The three error families define what can go wrong. The three-tier verification strategy defines how Vigil degrades when runtime coverage is incomplete.

---

## Architecture Map

Offline construction:

- Stage 0: App Prior Extraction - `vigil.neuro.app_prior`, `core.platform_priors`
- Stage 1: UI Exploration - `vigil.neuro.explorer`, `vigil.neuro.ape_explorer`, `core.ui_parser`, `core.action_types`
- Stage 2: XML Normalization + State Abstraction - `vigil.neuro.state_abstractor`, `core.ui_compressor`, `core.ui_selectors`
- Stage 3: Hierarchical FSM Construction - `vigil.neuro.fsm_builder`, `models.fsm`
- Stage 4: Semantic Grounding + DSL Guard Generation - `vigil.neuro.semantic_grounder`, `vigil.neuro.dsl_generator`, `vigil.neuro.widget_templates`
- Stage 5: Replay Verification + Confidence Scoring - `vigil.neuro.replay_verifier`, `symbolic.trajectory_verifier`

Online verification:

- Tier 1: Structural FSM verification - `symbolic.state_locator`, `symbolic.fsm_checker`
- Tier 2: DSL guards and invariants - `symbolic.dsl_evaluator`, `symbolic.intent_extractor`, `symbolic.invariant_checker`
- Tier 3: Template inheritance and micro-evolution - `neuro.evolution`, `symbolic.llm_fallback`

Compact acceptance rule:

```text
ALLOW iff (s_t, a_t, s') in delta
      and Reach(s', goal)
      and rho(s_t, a_t, s') >= theta_conf
      and eval(Gamma(s_t, a_t), o_t, intent, a_t) = true
      and forall phi in I(s', a_t): eval(phi, o_t, intent, a_t) = true
```

`DENY` means a transition, reachability, guard, or invariant violation is proven. `UNCERTAIN` means the verifier cannot prove safety because localization, replay confidence, binding, template trust, or predicate evaluation is incomplete.

---

## Fidelity App Notes

The controlled benchmark Android app should live in root-level `fidelity_app/`, separate from `src/vigil/`. It should be native Kotlin + Jetpack Compose, deterministic, emulator-friendly, and expose stable accessibility/test identifiers.

Provisionally named `VigilMarket`, it should cover the three error families with a compact shopping-style flow and maintain hidden evaluator artifacts in `fidelity_app/gold/`:

- `fsm.json`
- `guards.json`
- `tasks.json`

Use it as a calibration target for `state_id`, canonical action identity `<tau, q, v>`, transition extraction, template abstraction, DSL guard evaluation, and replay confidence. Do not wire it into the Python pipeline until the app and gold artifacts are stable.

Recommended app commands:

```bash
cd fidelity_app
./gradlew assembleDebug
./gradlew installDebug
adb -s emulator-5554 shell monkey -p com.vigil.market 1
```

---

## Working Habit

Before making code changes, inspect the relevant module and nearby tests. Prefer `rg` / `rg --files` for discovery. Keep tests proportional to risk and run the narrowest useful test set first, then broaden when touching shared behavior.
