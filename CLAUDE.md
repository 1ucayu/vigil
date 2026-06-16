# CLAUDE.md - Vigil Working Context

This file is intentionally short so Claude Code starts quickly and focuses on the current engineering task. The full historical context was preserved before slimming:

- Full Claude context: `docs/context/CLAUDE.full.md`
- Full Codex/Agents context: `docs/context/AGENTS.full.md`
- Architecture notes: `docs/architecture.md`
- Error taxonomy: `docs/error_taxonomy.md`
- Paper outline: `docs/nsdi_paper_outline.md`
- Live Overleaf paper source (read-only symlink): `overleaf/`
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

## Paper Source

The root-level `overleaf/` path is a local symlink to the Dropbox-backed Overleaf project. Treat it as read-only context: inspect `overleaf/main.tex`, `overleaf/body.tex`, `overleaf/refs.bib`, and related paper files when needed, but do not edit, format, generate, delete, or move anything under `overleaf/`. Put derived notes, generated documentation, or visual artifacts in `docs/` or `output_docs/` according to the existing repository rules.

---

## Current Priority

FSM construction now has a stable behavior-aware quotienting baseline. The next default paper/implementation focus can move to semantic grounding, DSL guard generation, and verifier integration, while preserving the FSM construction invariants below. Do not keep tuning the fidelity apps toward exact gold counts unless the user explicitly asks; residual splits should be explained before being optimized.

When touching FSM construction, focus on making the builder and validator agree on:

- `state_id`
- abstract-state templates
- selector semantics
- canonical action identity `<tau, q, v>`
- transition provenance
- replay confidence fields

Existing Settings traces are sufficient for the next development pass. Do not rerun the emulator just to collect more exploration data unless coverage is demonstrably missing, trace files are stale, or replay trials are needed to estimate `rho`.

Current verified snapshot after the AbstractState schema migration and behavioral quotient pass:

- New FSM JSON writes use `schema_version` 4, nested-only state payloads.
- Reader-side compatibility still accepts schema versions 2/3/4.
- Settings validation baseline: `total=385`, `ok=369`, `action_signature_mismatch=14`, `template_binding_missing=1`, `transition_not_in_fsm=1`.
- Fidelity replay baseline: `107/107 OK`.
- Behavioral quotient focused baseline: `183 passed`, `4 skipped` for `tests/test_behavioral_signature.py tests/test_behavioral_quotient.py tests/test_fsm_builder.py tests/test_replay_verifier.py tests/test_semantic_grounder.py`.
- Full suite baseline: `726 passed`, `4 skipped`, `1 failed`; the remaining known failure is `tests/test_llm_client.py::TestProxyProvider::test_proxy_images_fallback`, unrelated to FSM construction.
- Fidelity generated-vs-gold snapshot after canonical-action state quotienting: market `14/13`, bank `11/8`, chat `13/7`, clock `14/10`; all four generated FSMs have `0` high-trust `(state, canonical_action_key) -> multiple targets` conflicts.
- Known residual fidelity gaps: market `payment_dialog` / `remove_dialog` are marker/instrumentation mapping gaps; bank residual splits are form-status variants; chat `thread` is still split by form-status and optional repeated-row action presence; clock residual split is `timer_setup`; `system.back` and natural `$tick_elapsed_eq_duration` remain out-of-scope coverage/environment gaps.

---

## Non-Negotiable Engineering Rules

- Use `uv` only. Do not use direct `pip`, and do not create `requirements.txt`.
- Run Python commands through `uv run`.
- Static APK files are priors, not proof of transitions.
- Trace/replay evidence is required before adding an edge to `delta`.
- The LLM may add semantic labels, side-effect/semantic-binding metadata, DSL guard candidates, and provenance, but must not decide state equality, create static-only edges, assign replay confidence, or make runtime verdicts.
- XML/runtime traces remain the deterministic source of truth for fingerprinting, replay, selectors, and transition evidence.
- The compressed LLM view is only for prompting; never use it as the source of truth for localization or replay.
- Low-confidence or incomplete evidence should route to `UNCERTAIN`, not high-trust `ALLOW`.
- DSL strings are an executable guard backend, not the semantic source of truth. Prefer typed guard contracts plus admission validation before writing `Transition.guard`.
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

Current FSM action-scope boundary:

- `Sigma` is the agent/user-visible GUI action alphabet only: component-level actions (`click`, `long_press`, `input_text`, `scroll/swipe`) plus app/navigation actions needed for GUI control (`navigate_back`, `navigate_home`, launch/reset when used by tooling).
- Current offline FSM construction builds `delta_agent` only. Do not model `wait`, `time_elapsed`, `timeout`, or async/no-op observation as ordinary actions in `Sigma`.
- Time-driven or autonomous UI changes may be discussed as a future environment relation `delta_env` (`wait_until_idle`, `async_update`, `timeout`, `time_elapsed`), but they are out of scope for the current builder unless explicitly added as a separate extension.
- Volatile observations such as clock/stopwatch text, loading animation frames, timestamps, or list-content churn should not split abstract states unless they change the stable action schema, enabledness, guard facts, modal/dialog boundary, or safety-relevant side effects.
- Benchmark-only fast-forward buttons are ordinary component actions if they appear in the UI and have stable selectors; natural timer completion such as `$tick_elapsed_eq_duration` is an environment transition and should be treated as out of scope for current generated-vs-gold comparisons.

Current FSM abstraction/refinement baseline:

- State construction uses a post-build, verifier-preserving, state-only behavioral quotient, implemented mainly in `src/vigil/neuro/behavioral_signature.py`, `src/vigil/neuro/behavioral_quotient.py`, and `src/vigil/neuro/fsm_builder.py`.
- The quotient is a deterministic, trace-observation-compatible partition refinement, not exact textbook bisimulation/DFA minimization. States may merge when their observed `canonical_action_key -> target_block` maps are compatible; a final determinism guard preserves the verifier invariant.
- In paper notation, `\mathcal{P}` denotes the current state partition during refinement, and `\mathcal{P}^\star` denotes the final fixed-point partition. Use `\mathrm{Succ}_{\mathcal{P}}(\mathcal{B},u)` for the successor blocks of state block `\mathcal{B}` under canonical action `u \in \Sigma` during refinement, and `\mathrm{Succ}_{\mathcal{P}^\star}(\mathcal{B},u)` for the final quotient-state successor relation. The final determinism condition is `|\mathrm{Succ}_{\mathcal{P}^\star}(\mathcal{B},u)| <= 1`.
- Do not introduce any coarser action projection, second action alphabet, or quotient-specific successor notation in writing or implementation notes. Compactness comes from quotienting states, not weakening action labels.
- `compute_quotient_label()` is schema-oriented: it keeps activity/window/dialog boundaries, action-surface affordances and enabledness/checked/selected facts, coarse form status, coarse error/status facts, and repeated-row action slots. It excludes volatile text, literal title/contact/message values, raw EditText contents, bounds, capture-local element ids, and repeated-list row content.
- `_action_surface()` performs sibling-aware repeated-row canonicalization only inside the state-label action surface used by `compute_quotient_label()`, wildcarding varying row-instance segments under conservative repeated-row eligibility. This collapses named row-action surfaces such as per-contact message options without merging functional controls like pause/lap/reset, and it never changes `canonical_action_key()`.
- `canonical_action_key()` is the only action identity used for behavioral refinement, transition deduplication, provenance, replay, and runtime verification. The quotient is on states only; do not introduce a separate action alphabet or weaken canonical action identity.
- Do not hardcode fidelity app package names, resource ids, contacts, product names, timer labels, or other benchmark-specific strings in the abstraction. If a residual split remains, report whether it is caused by label fields, transition refinement, missing exploration coverage, or an environment transition before proposing a fix.

---

## DSL Guard Generation Direction

The next guard-generation implementation should be contract-first:

```text
existing FSM + trace evidence
-> transition evidence view
-> stable widget/semantic registry
-> typed GuardContract
-> DSL compilation
-> admission validation
-> attach admitted guard metadata to the FSM
```

Do not rebuild or reshape the FSM graph just to add guards. Existing `S`, `Sigma`, `delta`, canonical action identities, provenance, and replay confidence should remain stable. Guard generation enriches existing transitions with `Gamma`; it must not add edges, modify `state_id`, weaken `canonical_action_key()`, or change `rho`.

Recommended model direction:

- Keep `Transition.guard` as the executable DSL string used by `DSLEvaluator`.
- Add non-breaking metadata such as typed guard contracts, required intent slots, side-effect/semantic-binding metadata, admission status/reason, and provenance when implementing this pass.
- Build a stable widget registry from source observations before asking for guard candidates. Prefer aliases backed by `resource_id`, `content_description`, stable text role, synthesized class alias, or template alias; never admit guards that depend on capture-local `e_XXXX` handles unless they are only temporary evidence.
- Generate `GuardContract` objects first, then compile them to the current DSL grammar. The DSL grammar should not be expanded unless the typed contract cannot be expressed with existing predicates.
- Admission must parse the DSL, resolve element aliases against the source-state registry, verify `$intent.*` variables against the contract slot schema, evaluate literal source predicates when possible, and record rejection reasons.
- Side-effecting or irreversible actions (`send`, `pay`, `transfer`, `delete`, permission grants, irreversible confirms) require an admitted semantic/safety guard. If no guard can be admitted, runtime should return `UNCERTAIN`, not silently `ALLOW`.
- `cancel`, `back`, ordinary navigation, and passive scroll/open actions may have no guard when topology and confidence are sufficient.
- State invariants remain state-level checks in `AbstractState.invariant_specs`; transition guards are pre-action checks over the source screen, proposed action, and frozen intent.

APK static artifacts are useful guard-generation priors but never transition or verdict proof:

- Use manifest/activity labels, permissions, resource strings, string arrays, layout XML, menu/navigation resources, widget declarations, `inputType`, hints, and resource ids to enrich widget aliases, roles, value domains, and side-effect semantics.
- Static prior can suggest that `com.app:id/amount_input` is an amount field, a resource array is a closed-set option domain, or a `Transfer`/`Delete`/`Allow` label implies a side-effecting or authority-changing action.
- Runtime trace/XML evidence must still confirm element presence, source-state membership, action binding, and transition existence before a guard is admitted.
- Do not create edges, mark guards high-trust, or return runtime `ALLOW` from APK static evidence alone. Replay confidence still gates trust.

When an LLM is used for guard generation, prompt it to produce typed guard-contract candidates, not free-form DSL as the primary artifact. The prompt should include:

- strict task boundary: no new transitions, no state/action/confidence edits, no runtime verdicts;
- output JSON schema for required slots, predicates, side-effect/semantic-binding metadata, confidence, evidence, and rejection/admission notes;
- supported predicate vocabulary (`read`, `value`, `action`, `contains`, `count`, `in_state`, `time_in`) and readable UI properties;
- source state summary, target state summary, proposed canonical action, source widget registry, sibling outgoing transitions, source-to-target diff, and static app prior/resource hints;
- explicit instruction that pre-action guards may reference only the source screen, proposed action, and frozen `$intent.*` variables, not target-only elements;
- explicit fallback behavior: if evidence is insufficient, return a rejected/low-trust candidate with a reason instead of inventing selectors, slots, or literals.

Guard reports, rejected candidates, and audit artifacts belong under `output_docs/`.

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
- Stage 4: Semantic Grounding + Executable Guard Synthesis - `vigil.neuro.semantic_grounder`, `vigil.neuro.dsl_generator`, guard contract/registry/compiler/admission modules, `vigil.neuro.widget_templates`
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

Controlled benchmark Android apps live under root-level `fidelity_app/`, separate from `src/vigil/`. Each app subdirectory is its own native Kotlin + Jetpack Compose Gradle project with its own `gradlew`, package name, build outputs, and hidden evaluator artifacts.

Current fidelity app projects:

- `fidelity_app/vigilmarket/` (`com.vigil.market`)
- `fidelity_app/vigilbank/` (`com.vigil.bank`)
- `fidelity_app/vigilchat/` (`com.vigil.chat`)
- `fidelity_app/vigilclock/` (`com.vigil.clock`)

Each app maintains hidden evaluator artifacts in its own `gold/` directory:

- `fsm.json`
- `guards.json`
- `tasks.json`

Use these apps as calibration targets for `state_id`, canonical action identity `<tau, q, v>`, transition extraction, template abstraction, DSL guard evaluation, and replay confidence. Do not wire an app into the Python pipeline until the app and gold artifacts are stable.

Recommended app commands:

```bash
cd fidelity_app/vigilmarket && ./gradlew assembleDebug
cd fidelity_app/vigilbank && ./gradlew assembleDebug
cd fidelity_app/vigilchat && ./gradlew assembleDebug
cd fidelity_app/vigilclock && ./gradlew assembleDebug
```

---

## Working Habit

Before making code changes, inspect the relevant module and nearby tests. Prefer `rg` / `rg --files` for discovery. Keep tests proportional to impact and run the narrowest useful test set first, then broaden when touching shared behavior.
