# CLAUDE.md — Vigil

> **Place this file at**: `/Users/lucayu/Desktop/GitHub/vigil/CLAUDE.md`
> This is the master context for Claude Code. Read it **fully** before doing anything.
> It contains: project vision, research positioning, system architecture, implementation spec, and development conventions.

---

## 1. Project Identity

| Field | Value |
|-------|-------|
| **Title** | Vigil: Self-Evolving Neuro-Symbolic Runtime Verification for Mobile GUI Agents |
| **Author** | Luca Yu |
| **Email** | lucayu@connect.hku.hk |
| **Affiliation** | The University of Hong Kong (HKU) |
| **Repo path** | `/Users/lucayu/Desktop/GitHub/vigil` |
| **Git** | Already initialized |
| **Target venue** | NSDI 2026 style systems submission |

---

## 2. One-Paragraph Summary

Vigil is a **neuro-symbolic runtime verification system** for mobile GUI agents. Its paper narrative is organized around three mobile GUI error families: **GUI state and transition errors** (wrong screen, illegal action, dead end, loop), **GUI semantic binding errors** (wrong field, value, item, contact, address, or intent slot), and **GUI safety and side-effect errors** (structurally legal actions that violate user constraints or cause harmful irreversible effects). In the **offline construction phase**, Vigil consumes two evidence sources: APK static files (manifest, layout XML, strings/resources, permissions) and exploration trace files (runtime UI XML, screenshots, action logs). Deterministic XML/static/trace processing constructs a **per-app hierarchical Finite State Machine (FSM)**, while offline LLM calls add semantic labels, risk annotations, invariants, and grammar-checked **DSL guards**. The FSM is verified by test-case generation and on-device replay. In the **online symbolic phase**, a lightweight engine checks every proposed GUI action before execution using FSM structure, DSL guards, task-state progress, invariants, and confidence thresholds, returning **ALLOW / DENY / UNCERTAIN** without a runtime LLM in the common path. Vigil is also **self-evolving**: unseen but structurally similar UI states inherit parameterized templates, while truly novel states trigger asynchronous micro-evolution and are cached back into the FSM bundle after validation.

---

## 3. Core Research Insight

Every mobile app's UI is essentially a **finite state machine** — screens are states, user actions are transitions. This FSM can be **automatically constructed** (neuro) and used for **formal verification** (symbolic). Even highly dynamic apps (UberEats, Taobao) have **static structural skeletons** — "different restaurant pages" share the same structural state template. Vigil separates **structure** (cacheable, formally verifiable) from **content** (runtime-bound via parameterized guards).

### Neuro-Symbolic Division of Labor

```
Offline construction                    Online verification
────────────────────                    ───────────────────
APK static prior extraction       →     State localization
Trace XML/screenshot processing   →     FSM transition checks
XML-derived state abstraction     →     Reachability / loop checks
Offline LLM semantic grounding    →     DSL predicate evaluation
Replay validation                 →     Confidence-gated verdicts
```

### Paper Spine: Three Mobile GUI Error Families

Vigil's NSDI-style narrative should present the system as a verifier that covers three increasingly strict questions:

| Error Family | Core Failure | Verification Question | Vigil Mechanism |
|--------------|--------------|-----------------------|-----------------|
| **1. GUI State and Transition Errors** | Agent acts from the wrong screen, clicks an illegal element, reaches a dead end, or loops. | Can the agent legally move from this GUI state to the next one? | FSM state localization, transition validity, reachability, loop detection, replay confidence. |
| **2. GUI Semantic Binding Errors** | Agent reaches the right UI structure but binds the wrong value, field, item, contact, address, or intent slot. | Is the agent doing the right thing on this screen for the frozen user intent? | DSL guards, `$intent.*` variables, Task State Machine, parameterized templates. |
| **3. GUI Safety and Side-Effect Errors** | Agent performs a formally legal GUI action that violates constraints or causes harmful side effects. | Is this action safe to commit to the real device? | Safety guards, frozen intent, state/action invariants, irreversible-action monitor, runtime decision engine. |

Keep the distinction crisp: the **three error families** define what can go wrong, while the **three-tier verification strategy** defines how Vigil degrades when runtime coverage is incomplete.

### Formal Paper Model (Use in Writing and Code)

Use the following notation consistently in the paper and implementation notes:

```text
M_A = <S, s0, Sigma, delta, Gamma, I, rho>
```

| Symbol | Meaning | Implementation Anchor |
|--------|---------|-----------------------|
| `S` | Finite set of abstract GUI states. Each state represents a class of screens with the same stable GUI structure. | `AppFSM.states`, `AbstractState` |
| `s0` | Initial app state. | `AppFSM.initial_state` |
| `Sigma` | Canonical GUI action alphabet. An action is conceptually `<tau, q, v>`, where `tau` is the action type, `q` is the target widget/container, and `v` is an optional value. | `ActionType`, action dictionaries in `Transition.action` |
| `delta` | Action-labeled GUI transition relation, `delta subseteq S x Sigma x S`. | `AppFSM.transitions`, `networkx.DiGraph` edges |
| `Gamma` | Guard map from state-action pairs to DSL formulas, `Gamma:S x Sigma -> Phi`. Guards check semantic binding before execution. | `Transition.guard`, `DSLEvaluator` |
| `I` | Invariant map from state-action pairs to sets of DSL formulas, `I:S x Sigma -> 2^Phi`. Invariants encode state, action, and side-effect constraints. | `AbstractState.invariant_specs`, `InvariantChecker` |
| `rho` | Replay confidence map, `rho:delta -> [0,1]`. Low-confidence edges route to `UNCERTAIN`, not high-trust `ALLOW`. | `Transition.confidence`, `FsmChecker` |

Writing rule: describe `M_A` as a **DSL-guarded, confidence-annotated EFSM** built on the transition-system view underlying Kripke structures. A full Kripke structure additionally materializes atomic propositions `AP` and a labeling function `L:S -> 2^AP`; Vigil instead evaluates DSL predicates at runtime as transition contracts. Each verified transition may be read as a Hoare-style contract:

```text
{ Gamma(s, a) } a { I(s', a) }
```

This contract interpretation is the paper bridge between FSM topology, DSL semantic binding, and safety invariants.

### Offline FSM Construction Principle

The offline FSM builder must separate **graph truth** from **semantic annotation**.

- **Graph truth is evidence-driven.** State identity, action identity, and transition existence come from APK static priors plus runtime trace XML/action logs/replay. The LLM must not decide state equality, create static-only edges, assign replay confidence, or make runtime verdicts.
- **Semantic annotation is LLM-assisted.** Because Android apps rarely provide official task APIs or docs, an offline LLM may summarize static files into an App Prior Card, label screen function and icon-only widgets, detect parameterized templates, generate DSL guard candidates, and classify risk. All generated guards must be grammar-checked and stored with provenance.
- **Static files are priors, not proofs.** Manifest activities, parent activities, launcher intent-filters, permissions, strings, and layout XML provide activity names, initial-state hints, widget-type ground truth, hierarchy skeletons, and capability boundaries. They guide abstraction and guards, but transitions require trace or replay evidence.
- **Trace files are behavioral evidence.** Runtime UI XML and screenshots determine observed states, canonical actions, and edges. Screenshots fill visual semantics missing from XML; XML remains the primary source for deterministic matching.

---

## 4. System Architecture

### 4.0 Error-Family-to-Module Mapping

| Error Family | Primary Modules | Offline Support | Online Enforcement |
|--------------|-----------------|-----------------|--------------------|
| GUI State and Transition Errors | `models/fsm.py`, `models/action.py`, `core/ui_parser.py`, `core/action_types.py`, `symbolic/state_locator.py`, `symbolic/fsm_checker.py`, `symbolic/trajectory_verifier.py` | `neuro/explorer.py`, `neuro/state_abstractor.py`, `neuro/fsm_builder.py`, `neuro/replay_verifier.py` build and validate the topology. | Locate current state, check legal transition, reject unreachable paths, detect loops, return UNCERTAIN on low-confidence transitions. |
| GUI Semantic Binding Errors | `models/dsl.py`, `models/state.py`, `symbolic/dsl_evaluator.py`, `symbolic/intent_extractor.py`, `symbolic/trajectory_verifier.py`, `symbolic/decision_engine.py` | `neuro/semantic_grounder.py`, `neuro/dsl_generator.py`, `neuro/widget_templates.py`, `neuro/evolution.py` create semantic profiles, guards, and dynamic templates. | Freeze intent, bind `$intent.*` variables, evaluate guards, track multi-step task progress, inherit and bind templates for dynamic content. |
| GUI Safety and Side-Effect Errors | `symbolic/decision_engine.py`, `symbolic/invariant_checker.py`, `symbolic/dsl_evaluator.py`, `symbolic/fsm_checker.py`, `integration/agent_runner.py`, `scripts/verify_action.py` | Replay verification and guard generation identify high-risk transitions, irreversible actions, and state invariants. | Enforce safety guards and invariants before execution; return DENY or UNCERTAIN for risky, under-specified, or low-confidence actions. |

### 4.1 Offline Pipeline (Construction Layer — 6 Stages)

Keep the root implementation skeleton concise. The deeper literature survey, design justification, and formal definitions live in `docs/references/neuro_symbolic_architecture_survey.md`.

**Stage 0: App Prior Extraction** (`vigil.neuro.app_prior`, `core.platform_priors`)
- Technical challenge: consumer Android apps rarely expose public control APIs or complete documentation, so static APK files are the best available prior.
- Implementation role: parse manifest, layout XML, strings/resources, permissions, launcher activity, parent activity metadata, and widget declarations into an `AppPrior` / App Prior Card.
- Artifact: static priors for entry state, activity hierarchy, widget-type templates, permission/capability boundaries, and risk categories. Static files guide construction but never create FSM transitions by themselves.

**Stage 1: UI Exploration** (`vigil.neuro.explorer`, `vigil.neuro.ape_explorer`, `core.ui_parser`, `core.action_types`)
- Technical challenge: Android apps expose huge action spaces, nondeterministic transitions, scroll-dependent widgets, system dialogs, and state aliases caused by dynamic content.
- Implementation role: enumerate candidate actions from accessibility attributes, execute bounded BFS/DFS or APE-style exploration, capture `(screen_before, action, screen_after)` triples, and preserve screenshots/XML/action metadata for later replay.
- Artifact: raw observation set `O`, candidate action alphabet `Sigma`, trace multiset `Tau`, and low-level transition samples under `data/apps/<app_name>/`. Existing traces can be reused for FSM construction; rerun the emulator only when coverage is missing, the trace schema changed, or replay confidence must be measured.

**Stage 2: XML Normalization + State Abstraction** (`vigil.neuro.state_abstractor`, `core.ui_compressor`, `core.ui_selectors`)
- Technical challenge: exact screenshots over-split dynamic pages, while coarse fingerprints can merge semantically different states such as payment confirmation and message confirmation.
- Implementation role: maintain two views of each UI tree. The construction view keeps deterministic features for fingerprinting and replay: class/tag, resource-id, content-desc, actionability, enabled/checked/selected flags, parent-child path, coarse bounds, and stable visible text. The LLM view is a compressed AndroidArena-style tree: layout-only or invisible nodes are removed, non-functional wrappers are merged, actionable components are kept, and local handles are assigned.
- Artifact: abstract states `S`, localization fingerprints, selector handles, stable state invariants, and static/dynamic container labels. Never use the compressed LLM view as the source of truth for fingerprinting or replay.

**Stage 3: Hierarchical FSM Construction** (`vigil.neuro.fsm_builder`, `models.fsm`)
- Technical challenge: flat GUI graphs explode because repeated fragments, list items, dialogs, and nested activities create many near-duplicate paths.
- Implementation role: combine static priors and trace evidence to build a hierarchy `App > Activity > Fragment > Component`; deduplicate raw screens into `AbstractState` nodes; canonicalize actions as `<tau, q, v>`; attach observed transitions to `networkx.DiGraph`; and represent repeated dynamic item flows with `SubFsmTemplate` rather than enumerating every item.
- Artifact: per-app `AppFSM = (S, s0, Sigma, delta)` plus hierarchy metadata, transition provenance, dynamic sub-FSM templates, and selector mappings. Static priors may name states and refine abstraction, but an edge enters `delta` only through trace or replay evidence.

**Stage 4: Semantic Grounding + DSL Guard Generation** (`vigil.neuro.semantic_grounder`, `vigil.neuro.dsl_generator`, `vigil.neuro.widget_templates`, `models.dsl`)
- Technical challenge: topology alone cannot prove semantic correctness; the verifier must know which recipient, amount, field, contact, item, or constraint the action binds.
- Implementation role: use offline LLM calls over the App Prior Card, compressed XML, screenshots, and widget templates to label page function, icon-only widgets, parameterized templates, required intent variables, high-risk actions, and grammar-valid DSL predicates. Parse every guard with `output_docs/dsl_grammar.lark` before admitting it to the bundle.
- Artifact: transition guard map `Gamma: S x Sigma -> guard`, required `$intent.*` bindings, guard provenance, semantic profiles, high-risk action labels, and candidate state/action invariants.

**Stage 5: Replay Verification + Confidence Scoring** (`vigil.neuro.replay_verifier`, `symbolic.trajectory_verifier`)
- Technical challenge: explored GUI transitions may be flaky because of timing, permissions, network state, animation, or hidden app state.
- Implementation role: enumerate bounded FSM paths, replay them on-device, validate observed target states, mine replay-stable invariants, and estimate transition confidence rather than assuming one successful trace proves correctness.
- Artifact: verified FSM+DSL bundle with `rho(s, a, s') = success_count / trial_count`; low-confidence transitions remain usable only through `UNCERTAIN`.

### 4.2 Online Engine (Symbolic Layer — Three-Tier Verification)

Vigil's paper model for app `A` is:

```text
M_A = <S, s0, Sigma, delta, Gamma, I, rho>
```

where `S` is abstract states, `s0` the initial state, `Sigma` canonical actions, `delta` the transition relation, `Gamma` DSL guards, `I` state/action/side-effect invariants, and `rho` replay confidence. Parameterized templates for dynamic UI regions are implementation metadata stored alongside the model bundle.

At runtime, a screen observation `o_t` is localized by:

```text
alpha(o_t) -> (s_t, p_loc)
```

The common path is pure symbolic and deterministic:

**Tier 1: Structural FSM Verification** (`symbolic.state_locator`, `symbolic.fsm_checker`)
- Localize `o_t` to `s_t`; unknown or fuzzy-only localization returns `UNCERTAIN`.
- Check `(s_t, a_t, s') in delta`; a missing transition returns `DENY`.
- Check reachability from `s'` to the goal and enforce replay confidence `rho(s_t, a_t, s') >= theta_conf`.

**Tier 2: Parameterized Guard + Invariant Verification** (`symbolic.dsl_evaluator`, `symbolic.intent_extractor`, `symbolic.invariant_checker`)
- Bind frozen user intent into `$intent.*` variables and evaluate transition guards in `Gamma`.
- Evaluate state/action invariants in `I`, including irreversible-action and safety constraints.
- Missing required bindings or inconclusive predicate reads return `UNCERTAIN`; proven guard or invariant violations return `DENY`.

**Tier 3: Template Inheritance + Micro-Evolution** (`neuro.evolution`, `symbolic.llm_fallback`)
- For structurally similar unseen states, `inherit_and_bind` creates a low-trust state from an existing template without blocking future exact localization.
- For genuinely novel states, return `UNCERTAIN` and trigger asynchronous evolution; new states increase coverage but require replay confidence before high-trust `ALLOW` decisions.
- Runtime LLM fallback is optional and outside the common symbolic path.

**Compact Acceptance Rule:**

```text
ALLOW iff (s_t, a_t, s') in delta
      and Reach(s', goal)
      and rho(s_t, a_t, s') >= theta_conf
      and eval(Gamma(s_t, a_t), o_t, intent, a_t) = true
      and forall phi in I(s', a_t): eval(phi, o_t, intent, a_t) = true
```

`DENY` means a transition, reachability, guard, or invariant violation is proven. `UNCERTAIN` means the verifier cannot prove safety because localization, replay confidence, binding, template trust, or predicate evaluation is incomplete.

### 4.3 Central Agent: Lifecycle Management
- Storage: each app → verified FSM + DSL bundle (JSON)
- Version tracking: app version vs FSM version
- Incremental update: after app update, re-explore only changed screens
- Cross-device sharing: same app's FSM distributable to multiple devices
- Evolution log: track all Tier 3 evolution events for analysis

### 4.4 Current Implementation Status and Bottleneck

As of May 2026, the repo already contains the main construction modules: `neuro/app_prior.py` extracts APK static priors; `core/ui_compressor.py` creates compact UI trees for LLM prompts; `neuro/fsm_builder.py` builds hierarchical FSMs with structural fingerprints, transition merging, dynamic-container classification, and sub-FSM templates; `neuro/semantic_grounder.py` injects static context into optional multimodal LLM labeling; `neuro/dsl_generator.py` generates grammar-checked guards with layout XML fallback; and `neuro/replay_verifier.py` defines the replay-confidence interface.

The current bottleneck is **FSM construction and validation alignment**, not UI exploration. Existing Settings traces are sufficient for the next development pass. Do not rerun the emulator just to collect more UI exploration data until the builder and validator agree on current `state_id`, template, selector, and canonical action semantics. Rerun exploration only when coverage is demonstrably missing, trace files are stale, or replay trials are needed to estimate `rho`.

Current schema status: `AbstractState` has completed the migration to schema v4 nested canonical storage. New FSM writes are nested-only; `AppFSM.deserialize()` still accepts v2 flat bundles and v3 nested-plus-flat-mirror bundles. New implementation work should use nested state paths directly and treat flat names only as legacy input aliases.

Latest validation snapshot after the schema migration: Settings validation remains `total=385`, `ok=369`, with `action_signature_mismatch=14`, `template_binding_missing=1`, and `transition_not_in_fsm=1`; Fidelity replay remains `107/107 OK`. The full pytest suite is at `654 passed`, `3 skipped`, `1 failed`, where the remaining failure is the unrelated `tests/test_llm_client.py::TestProxyProvider::test_proxy_images_fallback`.

---

## 5. Related Work Positioning

### 5.1 Direct Competitors

| Work | Venue | What they do | Vigil's advantage |
|------|-------|-------------|-------------------|
| **VeriSafe Agent** | MobiCom'25 | Horn clause DSL + autoformalization | Manual DSL (1 app demo). Vigil auto-generates, scales |
| **V-Droid** | MobiCom'26 | LLM-as-verifier (prefilling-only) | No formal guarantee, needs runtime LLM. Vigil is symbolic |
| **Agent-SAMA** | AAAI'26 | 4 LLM agents + online FSM for planning | FSM online-built (unreliable), 4 runtime LLMs. Vigil offline + zero LLM |
| **ActionEngine** | arXiv'26 | Offline crawling → per-app FSM for planning | FSM for planning not verification; no semantic guards; no correctness proof |
| **SPlanner** | arXiv'25 | Manual EFSM → LLM planning | Manual modeling is #1 limitation (authors admit). Vigil auto-constructs |
| **AgentSpec** | ICSE'26 | Runtime enforcement DSL | No FSM structure, no online evolution. Rules pre-defined |
| **Pro2Guard** | arXiv'25 | DTMC from traces → probabilistic model checking | No formal FSM verification; cold start problem. Potentially complementary |
| **AGrail** | ACL'25 | Adaptive safety checks via TTA with 2 LLMs | Fully neural, no formal guarantee. Our Tier 3 is the symbolic analog |

### 5.2 Key Differentiation Summary

| Dimension | VeriSafe | V-Droid | Agent-SAMA | ActionEngine | **Vigil** |
|-----------|----------|---------|-----------|-------------|-----------|
| Formal guarantee | Horn clause | None | None | None | **FSM + DSL** |
| Automation | Manual DSL | Fully auto | LLM online | LLM offline | **LLM offline** |
| FSM purpose | N/A | N/A | Planning | Planning | **Verification** |
| Semantic check | Yes (manual) | LLM scoring | No | No | **DSL guards (auto)** |
| Runtime LLM | Yes | Yes | 4 agents | 1 call | **None (Tier 1-2)** |
| Self-evolution | No | No | No | Failure re-ground | **Three-tier** |
| On-device | Yes | No | No | Partial | **Yes** |
| FSM correctness proof | N/A | N/A | None | None | **Replay verification** |

### 5.3 Unique Contribution Gap (confirmed via comprehensive survey)

1. **No existing work combines formal FSM verification with online self-evolution** — VeriSafe is formal but static; AGrail is adaptive but neural; AgentSpec has DSL but no evolution
2. **No existing work has tiered degradation from symbolic to neural** — all works are either purely symbolic or purely neural
3. **No existing work frames the verification model as a self-evolving artifact** — MAGNET/HyMEM evolve agent memory, but nobody evolves the verifier's model

### 5.4 Positioning Metaphor

**Vigil is not a GUI agent — it is the safety layer for ANY GUI agent.**
- ActionEngine = a better driver (faster, more accurate task completion)
- Vigil = the car's ABS + collision warning (works regardless of driver skill)
- Can wrap ActionEngine, Agent-SAMA, AppAgent, or any future agent without modification

---

## 6. Contributions (C1–C5)

**C1: Automatic Per-App FSM+DSL Construction**
- Solves: VeriSafe manual DSL + SPlanner manual EFSM
- Challenge: state abstraction granularity, LLM guard quality

**C2: FSM Verification via Test Case Generation + Replay**
- Solves: ActionEngine has no FSM correctness proof
- Method: symbolic execution → test cases → real device replay → confidence scoring

**C3: Three-Tier Self-Evolving Runtime Verification**
- Solves: static verification can't handle dynamic apps
- Novel: formal verification model that self-evolves (no existing work does this)

**C4: Lightweight On-Device Deployment (No Runtime LLM for Tier 1-2; Optional LLM Fallback on Uncertain)**
- Solves: existing verifiers need a runtime LLM on every action
- Tier 1-2 remain pure-symbolic; LLM is consulted only when the symbolic layer returns UNCERTAIN

**C5: Central Agent for FSM+DSL Lifecycle Management**
- Solves: no existing work discusses model maintenance
- Includes: version tracking, incremental update, cross-device sharing, evolution log

### 6.1 NSDI-Style Paper Outline

Use a compact systems-paper structure. The taxonomy belongs in the motivation, and the runtime section should mirror the three error families.

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

Recommended subsection spine:

| Section | Key Subsections |
|---------|-----------------|
| Motivation and Design Goals | Mobile GUI agent failure taxonomy; why LLM judges, neural verifiers, and hand-authored rules fall short; design requirements. |
| Vigil Overview | Threat model and assumptions; neuro-symbolic architecture; offline construction vs online verification. |
| Offline Model Construction | UI exploration and state abstraction; FSM construction; DSL guard generation; replay-based verification. |
| Online Runtime Verification | State/transition verification; semantic binding verification; safety/side-effect verification; uncertainty and micro-evolution. |
| Evaluation | Experimental setup; overall effectiveness; per-error-family analysis; latency/resource cost; ablation study. |

---

## 7. Key Methodological References

**A Case for Learned Cloud Emulators** (HotNets 2025, UMich + HKU + Berkeley) — methodologically isomorphic:
- Both: unstructured knowledge -> formal state machine -> constrained generation -> symbolic verification
- Three inspirations: (1) hierarchical SM (VPC > Subnet > VM ~= App > Activity > Fragment), (2) formal grammar for constrained generation, (3) automated alignment via symbolic execution
- Their domain: cloud APIs. Our domain: mobile GUI. Same methodology.

**DroidBot** — demonstrates the practical Android pipeline of collecting screenshots plus UIAutomator hierarchy, identifying GUI states, and building a state transition graph from observed actions. Vigil should keep this evidence-backed graph construction idea, but use the graph for verification rather than testing alone.

**Stoat / Guided Stochastic Model-Based GUI Testing** — motivates static event identification, weighted exploration, and stochastic model construction. Vigil is not simply Stoat: Stoat learns a model to generate tests, while Vigil constructs a confidence-annotated EFSM for runtime verification and attaches semantic DSL guards.

**APE** — the key inspiration for adaptive GUI tree abstraction and refinement. Vigil should start with XML-derived structural abstraction, then refine when one abstract state produces conflicting successors or unsafe semantic merges, and coarsen into templates when differences are only dynamic content.

**AndroidArena / Understanding the Weakness of LLM Agents within a Complex Android Environment** — useful for XML compression and action grounding: remove redundant hierarchy information before LLM prompting, keep compact component handles, and map handles back to XPath/selector-like identities. Vigil should use this only for the LLM view, not for deterministic fingerprinting or replay.

**Static Android GUI analysis (GATOR / WTG / ProMal-style work)** — supports using manifest, layout XML, resources, activities, and permissions as static priors. These analyses can over-approximate possible windows/transitions, so Vigil treats static information as naming, hierarchy, widget, and risk prior rather than edge proof.

**Agent-SAMA** — useful contrast: it builds and updates a natural-language FSM online for planning and recovery with multiple LLM agents. Vigil instead builds an offline, replay-checked FSM+DSL bundle and keeps the common runtime path symbolic.

**Angluin L* Algorithm** (spirit) — FSM construction can be viewed as active model learning: exploration/replay are membership-style queries, while abstraction refinement resolves counterexamples.

**HyMEM** (arXiv 2026) — hybrid symbolic + continuous graph representation; graph evolution via node add/update/replace. Directly relevant to Tier 3 evolution.

**Pro2Guard** (arXiv 2025) — DTMC from traces + probabilistic model checking. Potentially complementary: Vigil FSM topology plus transition probabilities can become a probabilistic FSM.

---

## 8. Advisor Feedback & Responses

### Advisor concern (2026-03-26): Dynamic apps can't be fully pre-modeled as FSM.

**Questions raised:**
1. What semantic granularity for FSM? → Separate structure (cacheable) from content (runtime-bound)
2. How to handle sequential dependency (milk tea ordering)? → Task State Machine with intent checklist
3. When retrieval vs when evolution? → Three-tier degradation with structural similarity as gate

**Resolution:** Three-tier verification architecture (§4.2). Even UberEats has a static navigation skeleton; "different restaurant pages" share the same structural template.

### Advisor concern: Mobile GUI ≠ special, method isn't mobile-specific.

**Response (Route A — generalize framing):** Framework is platform-agnostic (any GUI with accessibility API). Android mobile is first instantiation because: (1) side effects most severe (real money, real messages), (2) deployment constraints harshest (resource-limited), (3) Accessibility Service is mature infrastructure.

---

## 9. Why GUI Level (not API Level)?

Most consumer mobile apps **don't expose public APIs for controlling their own functionality**. WeChat has no `sendMessage()` API; Alipay has no `transferMoney()` endpoint. These platform APIs are for developing ON the platform (mini-programs), not for externally controlling the app. Trend is further restriction: Google killed Photos Library API in March 2025.

**Clarification:** Vigil's methodology is not GUI-specific. For services with public APIs (cloud, Slack, GitHub), the same state-machine approach applies at API level (as Learned Cloud Emulators demonstrates). GUI/Accessibility is the fallback when APIs are unavailable.

---

## 10. Development Environment & Tooling

### 10.1 Stack Overview

| Component | Language | Key Libraries |
|-----------|----------|---------------|
| UI Exploration (Stage 1) | Python 3.11+ | `uiautomator2`, `adbutils`, `Pillow` |
| State Abstraction (Stage 2) | Python | `anthropic` / `openai` SDK |
| FSM Construction (Stage 3) | Python | `networkx` (DiGraph) |
| DSL Generation (Stage 4) | Python | `lark` (formal grammar parser), LLM SDK |
| Replay Verification (Stage 5) | Python | `uiautomator2`, `networkx` |
| Runtime Verifier (Online) | Python (prototype) | `networkx`, `lark` |
| Android Integration | Kotlin (future) | Android Accessibility Service |
| Testing & Eval | Python | `pytest`, AndroidWorld framework |
| Visualization | Python | `matplotlib`, `graphviz` |

### 10.2 Package Management

- **Package manager**: **uv** (https://docs.astral.sh/uv/) — the ONLY package manager
- **Virtual environment**: `.venv/` in project root, managed by uv
- **Package metadata**: `pyproject.toml` (PEP 621, hatchling build backend)
- **Do NOT** use pip directly, do NOT create `requirements.txt`. Everything through `pyproject.toml` + `uv`.

### 10.3 Bootstrap Commands

```bash
cd /Users/lucayu/Desktop/GitHub/vigil

# Sync project (creates .venv + installs all deps including dev)
uv sync --group dev

# Install pre-commit hooks
uv run pre-commit install

# Run commands via uv run (handles Python path correctly)
uv run pytest tests/
uv run vigil-explore --app com.android.settings --steps 20
```

### 10.4 `pyproject.toml`

See `pyproject.toml` in repo root — canonical source of truth for dependencies and tool config.

---

## 11. Directory Structure

```
vigil/
├── CLAUDE.md                       # ← THIS FILE
├── README.md
├── pyproject.toml
├── uv.lock                         # generated by uv
├── .venv/                          # local virtualenv (gitignored)
├── .gitignore
├── .pre-commit-config.yaml
│
├── configs/
│   ├── default.yaml                # default config (LLM model, timeouts, thresholds)
│   ├── android_platform.yaml       # Android SDK platform priors (widget templates, dialogs)
│   └── apps/                       # per-app config overrides
│       ├── settings.yaml
│       ├── wechat.yaml
│       └── alipay.yaml
│
├── src/
│   └── vigil/
│       ├── __init__.py             # exports __version__ = "0.1.0"
│       ├── py.typed                # PEP 561 marker
│       │
│       ├── neuro/                  # OFFLINE: FSM construction pipeline
│       │   ├── __init__.py
│       │   ├── app_prior.py       # Stage 0: App Prior Extraction (Manifest/APK)
│       │   ├── explorer.py         # Stage 1: UI Exploration (BFS/DFS via uiautomator2)
│       │   ├── ape_explorer.py     # APE-backed exploration adapter
│       │   ├── ape_runner.py       # APE process/device runner
│       │   ├── ape_parser.py       # APE output parser
│       │   ├── state_abstractor.py # Stage 2: State Abstraction (fingerprint)
│       │   ├── semantic_grounder.py# Stage 4: Semantic Grounding (multimodal LLM)
│       │   ├── fsm_builder.py      # Stage 3: Hierarchical FSM Construction
│       │   ├── dsl_generator.py    # Stage 4: DSL Semantic Guard Generation
│       │   ├── widget_templates.py # Widget guard template lookup (from YAML)
│       │   ├── risk_policy.py      # Permission/action risk classification
│       │   ├── scope_policy.py     # Exploration and verification scoping
│       │   ├── selector_resolution.py # Robust selector/action target mapping
│       │   ├── replay_verifier.py  # Stage 5: FSM Verification via Replay
│       │   └── evolution.py        # Tier 3: Online Micro-Evolution engine
│       │
│       ├── symbolic/               # ONLINE: Runtime verification engine
│       │   ├── __init__.py
│       │   ├── state_locator.py    # Screen → FSM state mapping (fingerprinting + similarity)
│       │   ├── fsm_checker.py      # Tier 1: FSM structural verification
│       │   ├── dsl_evaluator.py    # Tier 2: DSL semantic verification (guard eval)
│       │   ├── decision_engine.py  # Combined ALLOW / DENY / UNCERTAIN + tier routing
│       │   ├── intent_extractor.py # $intent.* variable extraction from instructions
│       │   ├── llm_fallback.py     # LLM fallback for UNCERTAIN results
│       │   ├── trajectory_verifier.py # Multi-step action sequence verification
│       │   └── invariant_checker.py# State invariant checking (Daikon-style)
│       │
│       ├── models/                 # Data structures & serialization
│       │   ├── __init__.py
│       │   ├── fsm.py             # AppFSM, AbstractState, Transition, SubFsmTemplate
│       │   ├── dsl.py             # DSL guard data structures
│       │   ├── state.py           # RawScreen / Screen runtime observations
│       │   ├── action.py          # Action type definitions & templates
│       │   └── schemas/           # JSON schemas for FSM/DSL bundles
│       │       ├── fsm_schema.json
│       │       └── dsl_schema.json
│       │
│       ├── core/                   # Shared utilities
│       │   ├── __init__.py
│       │   ├── ui_parser.py       # Accessibility tree XML → structured repr
│       │   ├── ape_ui_parser.py   # APE hierarchy parser/adapter
│       │   ├── ui_compressor.py   # Compact XML view for LLM prompts
│       │   ├── ui_selectors.py    # Stable selector and component-handle utilities
│       │   ├── action_types.py    # Action templates & enums
│       │   ├── screenshot.py      # Screenshot capture & annotation
│       │   ├── device_resolver.py # Emulator/physical-device selection
│       │   ├── llm_client.py      # Unified LLM client wrapper (Anthropic / OpenAI / Proxy)
│       │   ├── config.py          # Pydantic config models + YAML loader
│       │   └── platform_priors.py # Android SDK priors loader (from YAML)
│       │
│       └── scripts/                # CLI entry points
│           ├── __init__.py
│           ├── explore_app.py     # vigil-explore
│           ├── build_fsm.py       # vigil-build
│           ├── verify_fsm.py      # vigil-verify
│           └── visualize_fsm.py   # vigil-visualize
│
├── tests/                          # pytest test suite
│   ├── conftest.py                # shared fixtures (mock FSMs, sample trees, etc.)
│   ├── test_explorer.py
│   ├── test_state_abstractor.py
│   ├── test_fsm_builder.py
│   ├── test_dsl_evaluator.py
│   ├── test_fsm_checker.py
│   ├── test_decision_engine.py
│   └── test_evolution.py
│
├── eval/                           # Evaluation & benchmarks
│   ├── __init__.py
│   ├── run_benchmark.py           # main eval runner
│   ├── metrics.py                 # precision, recall, latency metrics
│   ├── tasks/                     # test task definitions
│   └── baselines/                 # baseline comparison configs
│
├── models/                         # Generated FSM+DSL bundles (gitignored at bundle level)
│   └── bundles/
│       ├── settings/
│       ├── wechat/
│       └── alipay/
│
├── data/                           # Exploration data (gitignored)
│   └── apps/
│       ├── settings/
│       │   ├── screens/           # screenshot PNGs
│       │   ├── trees/             # accessibility tree XMLs
│       │   └── traces/            # exploration trace logs
│       ├── wechat/
│       └── alipay/
│
├── docs/
│   ├── architecture.md            # Architecture diagrams & decisions
│   ├── error_taxonomy.md          # Paper taxonomy + module/benchmark mapping
│   └── nsdi_paper_outline.md      # Compact NSDI-style paper outline
│
├── output_docs/                    # Generated documentation and visualization artifacts
│   ├── dsl_grammar.lark           # Formal grammar for DSL guards
│   └── *.html                     # Exported FSM viewers
│
└── android/                        # Android Accessibility Service (Kotlin, future)
    └── VigilService/
        ├── app/src/main/
        │   ├── java/com/vigil/service/
        │   │   ├── VigilAccessibilityService.kt
        │   │   ├── StateExtractor.kt
        │   │   └── VerifierBridge.kt
        │   └── AndroidManifest.xml
        ├── build.gradle.kts
        └── settings.gradle.kts
```

**Logging:** Use `loguru` package for all logging. No custom logging module.

---

## 12. Key Data Models (Implementation Reference)

### AppFSM (`src/vigil/models/fsm.py`)

```python
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field

class HierarchyLevel(StrEnum):
    APP = "app"
    ACTIVITY = "activity"
    FRAGMENT = "fragment"
    COMPONENT = "component"

class ContainerType(StrEnum):
    STATIC = "static"
    DYNAMIC = "dynamic"
    NONE = "none"

class StateKind(StrEnum):
    NORMAL = "normal"
    DIALOG = "dialog"
    TERMINAL = "terminal"
    ERROR = "error"
    SYSTEM = "system"
    EXTERNAL = "external"

class StateSemanticProfile(BaseModel):
    # Legacy synthesized view. StateAnnotations is canonical storage.
    alt_text: str = ""
    page_function: str = ""
    expected_actions: list[str] = Field(default_factory=list)
    icon_labels: dict[str, str] = Field(default_factory=dict)
    generation_confidence: float = 0.0

class StateIdentity(BaseModel):
    functional_hash: str
    structural_hash: str | None = None
    secondary_hash: str | None = None
    identity_version: str = "v1"
    algorithm: str = "hybrid_ui_identity"

class AndroidStateContext(BaseModel):
    activity_name: str | None = None
    package_name: str | None = None
    window_type: str | None = None

class StateEvidence(BaseModel):
    raw_screen_ids: list[str] = Field(default_factory=list)
    construction_source: str = "observed_trace"
    first_seen_trace: str | None = None
    trust_level: str = "observed"

    @computed_field
    @property
    def observation_count(self) -> int:
        return len(self.raw_screen_ids)

class StateAbstraction(BaseModel):
    container_type: ContainerType = ContainerType.NONE
    container_selector: dict[str, Any] = Field(default_factory=dict)
    template_id: str | None = None
    template_role: str = "normal"
    parameter_schema: dict[str, str] = Field(default_factory=dict)
    parameter_bindings: dict[str, str] = Field(default_factory=dict)

class StateInvariant(BaseModel):
    expr: str
    confidence: float = 0.0
    source: str = "unknown"
    evidence_count: int = 0

class StateAnnotations(BaseModel):
    display_name: str = ""
    alt_text: str = ""
    page_function: str = ""
    expected_actions: list[str] = Field(default_factory=list)
    widget_aliases: list[dict[str, Any]] = Field(default_factory=list)
    generation_confidence: float = 0.0

class AbstractState(BaseModel):
    # Schema v4 canonical shape. Do not add new top-level flat fields.
    state_id: str
    name: str
    hierarchy_level: HierarchyLevel
    parent_state: str | None = None
    kind: StateKind = StateKind.NORMAL
    identity: StateIdentity
    android_context: AndroidStateContext = Field(default_factory=AndroidStateContext)
    evidence: StateEvidence = Field(default_factory=StateEvidence)
    abstraction: StateAbstraction = Field(default_factory=StateAbstraction)
    invariant_specs: list[StateInvariant] = Field(default_factory=list)
    annotations: StateAnnotations = Field(default_factory=StateAnnotations)
    legacy_invariants: list[str] = Field(default_factory=list)

    # Backward-compatible property aliases still accept/read old names:
    # fingerprint, structural_fingerprint, activity_name, raw_screens,
    # container_type, container_resource_id, sub_fsm_template_id,
    # semantic_profile, state_invariants, invariant_confidence, invariants.
    # These aliases must route to nested storage and must not be serialized
    # as schema v4 top-level state keys.

class ProvenanceEntry(BaseModel):
    trace_step_index: int = -1
    source_screen_id: str | None = None
    target_screen_id: str | None = None
    confidence_source: str = "observed"

class Transition(BaseModel):
    source: str
    target: str
    action: dict[str, Any]                         # {"type": "click", "target": ..., "target_text": ...}
    guard: str | None = None                       # DSL guard expression
    confidence: float = 0.0                        # replay success_count / total_trials
    low_trust: bool = False
    observed_count: int = 0
    provenance: list[ProvenanceEntry] = Field(default_factory=list)

class SubFsmTemplate(BaseModel):
    template_id: str
    source_state_id: str
    entry_fingerprint: str
    states: dict[str, AbstractState] = Field(default_factory=dict)
    transitions: list[Transition] = Field(default_factory=list)
    parameter_schema: dict[str, str] = Field(default_factory=dict)
    item_skeleton: str = ""

class AppFSM:
    def __init__(self, app_package: str):
        self.app_package = app_package
        self.graph = nx.DiGraph()
        self.states: dict[str, AbstractState] = {}
        self.transitions: list[Transition] = []
        self.initial_state: str | None = None
        self.version: str = "0.1.0"
        self.evolution_log: list[dict[str, Any]] = []
        self.sub_fsm_templates: dict[str, SubFsmTemplate] = {}

    def add_state(self, state: AbstractState): ...
    def add_transition(self, trans: Transition): ...
    def resolve_transition(self, from_state: str, action: dict): ...
    def is_valid_transition(self, from_state: str, action: dict) -> bool | None: ...
    def is_reachable(self, from_state: str, goal_state: str) -> bool: ...
    def serialize(self, path: str | Path): ...      # writes schema_version "4"
    @classmethod
    def deserialize(cls, path: str | Path) -> 'AppFSM': ...  # accepts schema 2/3/4
```

Serialization contract: schema v2 is flat-only legacy, schema v3 is transitional nested + flat mirrors, and schema v4 is the current nested-only canonical output. New code should consume nested fields directly and must not rely on top-level flat state keys outside the compatibility validator / alias boundary.

### DSL Guard Grammar (`output_docs/dsl_grammar.lark`)

```lark
start: guard

guard: predicate
     | predicate "&&" guard
     | predicate "||" guard
     | "!" predicate
     | "(" guard ")"

predicate: read_pred | time_pred | state_pred | value_pred
         | contains_pred | count_pred | action_pred

read_pred: "read(" ELEMENT "," PROPERTY ")" OP VALUE
time_pred: "time_in(" TIME "," TIME ")"
state_pred: "in_state(" STATE_NAME ")"
value_pred: "value(" ELEMENT ")" OP VALUE
contains_pred: "contains(" ELEMENT "," VALUE ")"
count_pred: "count(" ELEMENT ")" OP VALUE
action_pred: "action(" PROPERTY ")" OP VALUE

OP: "==" | "!=" | ">" | "<" | ">=" | "<="
ELEMENT: /[a-zA-Z_][a-zA-Z0-9_.:\/]*/
PROPERTY: /[a-zA-Z_][a-zA-Z0-9_]*/
STATE_NAME: /[a-zA-Z_][a-zA-Z0-9_]*/
VALUE: ESCAPED_STRING | NUMBER | "true" | "false" | "null" | INTENT_VAR
INTENT_VAR: /\$intent\.[a-zA-Z_][a-zA-Z0-9_]*/
TIME: /\d{2}:\d{2}/

%import common.ESCAPED_STRING
%import common.NUMBER
%import common.WS
%ignore WS
```

---

## 13. Default Configuration (`configs/default.yaml`)

```yaml
app:
  max_exploration_steps: 500
  screenshot_format: "png"
  exploration_strategy: "bfs"       # bfs | dfs | hybrid
  exploration_backend: "native"      # native | ape

device:
  type: auto                         # auto | emulator | physical
  serial: null                       # null = resolve via `type` below
  profile_name: default              # suffix for output dirs

ape:
  jar_path: "libs/ape.jar"
  device_jar_path: "/data/local/tmp/ape.jar"
  device_output_dir: "/sdcard/ape-output"
  running_minutes: 10
  ape_mode: "sata"                  # sata (CEGAR) | random

llm:
  provider: "proxy"                 # anthropic | openai | google | proxy
  model: "claude-sonnet-4.6"
  max_tokens: 4096
  temperature: 0.0
  proxy_base_url: "http://localhost:4141/v1"
  proxy_api_key: "dummy_key"
  proxy_model: "claude-sonnet-4.6"

state_abstraction:
  similarity_threshold: 0.85
  use_llm_fallback: true

verification:
  confidence_threshold: 0.7
  replay_trials: 3
  max_path_length: 10                # bounded path enumeration

runtime:
  fallback_on_uncertain: "user"      # user | llm | deny

evolution:
  enable_tier3: true
  similarity_threshold_inherit: 0.80  # above this, inherit_and_bind without LLM
  max_evolution_cache_size: 1000
  evolution_log_path: "data/evolution_log.jsonl"
```

---

## 14. `.gitignore`

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/

# Virtual environment
.venv/

# uv
uv.lock

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Project data (large, generated)
data/
models/bundles/

# Environment & secrets
.env
.env.*

# Test / coverage
.coverage
htmlcov/
.pytest_cache/
.mypy_cache/
.ruff_cache/
```

---

## 15. `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.6
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-json
      - id: check-added-large-files
        args: ['--maxkb=500']
```

---

## 16. Coding Conventions

- **Python 3.11+** with type hints on all public APIs
- **Docstrings**: Google style
- **Formatting**: `ruff format` (replaces black), line-length 100
- **Linting**: `ruff check` (see pyproject.toml config)
- **Type checking**: `mypy --strict` on `src/vigil/symbolic/` (critical verification path)
- **Testing**: `pytest`, target > 80% coverage on `symbolic/`
- **Config**: Pydantic models for validation, YAML files for user-facing config
- **Logging**: `loguru` — no stdlib `logging`, no custom logging module
- **Serialization**: JSON for FSM/DSL bundles (human-readable, inspectable)
- **Git commits**: Conventional commits — `feat:`, `fix:`, `refactor:`, `test:`, `docs:`
- **Imports**: Absolute only — `from vigil.models.fsm import AppFSM`
- **Package layout**: `src/vigil/` (src layout, PEP 621)

---

## 17. Common Commands

```bash
# --- Environment ---
uv venv .venv --python 3.11          # create venv
uv pip install -e ".[dev]"           # install with dev deps
uv pip install -e ".[dev,eval]"      # install with all extras
source .venv/bin/activate            # activate

# --- Quality ---
ruff check src/ tests/               # lint
ruff format src/ tests/              # format
mypy src/vigil/symbolic/             # type check critical path
pytest                               # run tests
pytest --cov=vigil                   # tests + coverage

# --- CLI tools (after install) ---
vigil-explore --app com.android.settings --steps 200
vigil-build --app settings --data data/apps/settings/
vigil-verify --app settings --trials 3
vigil-visualize --app settings --output docs/settings_fsm.png

# --- Pre-commit ---
pre-commit install
pre-commit run --all-files
```

---

## 18. Bootstrap Order (When Setting Up From Scratch)

Create files in this sequence:

1. `pyproject.toml` — from §10.4
2. `.gitignore` — from §14
3. `.pre-commit-config.yaml` — from §15
4. `src/vigil/__init__.py` — just `__version__ = "0.1.0"`
5. `src/vigil/py.typed` — empty file
6. All `__init__.py` stubs in subpackages (`neuro/`, `symbolic/`, `models/`, `core/`, `scripts/`)
7. `configs/default.yaml` — from §13
8. `output_docs/dsl_grammar.lark` — from §12
9. `tests/conftest.py` — shared fixtures
10. Run: `uv venv .venv --python 3.11 && uv pip install -e ".[dev]" && pre-commit install`

---

## 19. Implementation Priority & Development Notes

1. **Start with Settings app** — deterministic, no login, no network dependency, ideal for debugging.
2. **Current next step is FSM construction alignment** — fix builder/validator/state locator agreement before collecting more exploration data.
3. **Build in the order of the taxonomy** — first block state/transition errors, then semantic binding errors, then safety/side-effect errors.
4. **Keep `decision_engine.py` as the single runtime verdict point** — every check must collapse into ALLOW / DENY / UNCERTAIN.
5. **`src/` layout is mandatory** — all code under `src/vigil/`, never import from repo root.
6. **Core novelty = per-app FSM+DSL verifier + self-evolution** — avoid over-engineering Android infrastructure early.
7. **Verifier is agent-agnostic** — wraps ANY GUI agent as safety layer, does not replace agent.
8. **Replay non-determinism is expected** — use confidence scores, avoid chasing 100% reliability.
9. **State explosion mitigation** — hierarchy + adaptive XML abstraction + parameterized templates + bounded exploration.
10. **Never commit `data/` or `models/bundles/`** — large generated artifacts.
11. **All LLM calls are offline only** — runtime symbolic layer must NEVER call an LLM in the common path; Tier 3 is async and infrequent.
12. **uv is the only package manager** — no pip, no requirements.txt.
13. **Graph truth vs semantic annotation:** XML/static/trace/replay decide states, actions, and edges; LLMs label semantics, generate guards, and classify risk.
14. **Reference code and papers to borrow:**
    - DroidBot: screenshot + UIAutomator hierarchy -> observed state transition graph.
    - Stoat: static event identification, weighted exploration, and stochastic model-testing intuition.
    - APE: adaptive GUI tree abstraction and refinement to balance precision with state explosion.
    - AndroidArena: compressed XML for LLM prompting and component-handle action grounding.
    - VeriSafe: predicate patterns for payment, messaging, shopping, and other high-risk DSL guards.
15. **Fidelity app development:** keep the controlled Android benchmark app in a root-level `fidelity_app/` directory, separate from `src/vigil/`. Use native Kotlin + Jetpack Compose with simple deterministic UI, stable accessibility/test identifiers, seeded local data, and hidden `gold/` FSM/guard/task artifacts. The app is for FSM-construction fidelity testing on Android emulators; the Vigil explorer/evaluator remains Python.

### 19.1 Current FSM Construction Status (May 2026)

Implemented pieces already in the repo:

1. `neuro/app_prior.py` parses APK/manifest/static resources into `AppPrior` and saves static artifacts.
2. `core/ui_compressor.py` provides a compact UI tree for LLM prompts.
3. `neuro/fsm_builder.py` builds FSMs from traces, supports `state_id` paths, structural fingerprints, transition merging, hierarchy metadata, dynamic container classification, dialog/tab inferred transitions, and `SubFsmTemplate`.
4. `neuro/semantic_grounder.py` can add LLM-assisted state descriptions, icon labels, and invariant candidates with static context.
5. `neuro/dsl_generator.py` can generate grammar-validated guards and use layout XML as widget-template fallback.
6. `neuro/replay_verifier.py` defines the replay-verification interface, but the end-to-end `vigil-verify` pipeline is not fully wired yet.
7. `models/fsm.py` now stores `AbstractState` in nested canonical submodels and serializes new FSM bundles as schema v4 nested-only JSON.

Highest-priority alignment patches before the implementation section freezes:

1. **Builder-validator agreement:** update trace validation to use current `state_id`, template, selector, and canonical action semantics instead of legacy fingerprint matching.
2. **Canonical action identity:** compare the full `<tau, q, v>` signature so different widgets or values are not collapsed into the same transition.
3. **Static prior integration:** use `AppPrior` during state naming, initial-state selection, widget template lookup, permission/risk annotation, and abstraction refinement; never create static-only edges.
4. **APE-style refinement loop:** split states when one abstract state plus one canonical action yields conflicting successors, safety semantics conflict, or guard variables cannot be read; coarsen/template states that only differ by dynamic list/detail content.
5. **Three-valued DSL evaluation:** missing GUI elements, missing intent variables, parse failures, or unreadable predicates should produce `UNCERTAIN`, not ordinary semantic failure.
6. **Replay verifier completion:** replay bounded paths, update `Transition.confidence`, and preserve low-confidence transitions for `UNCERTAIN` handling.

Latest local validation snapshot for the relevant construction modules:

```bash
uv run pytest tests/test_models.py tests/test_visualize_fsm.py tests/test_validate_fsm.py
# 84 passed

uv run pytest tests/test_fsm_builder.py tests/test_state_identity.py tests/test_fsm_checker.py tests/test_invariant_checker.py tests/test_decision_engine.py tests/test_evolution.py tests/test_visualize_fsm.py
# 166 passed, 3 skipped

uv run pytest tests/
# 654 passed, 3 skipped, 1 failed
# Known unrelated failure: tests/test_llm_client.py::TestProxyProvider::test_proxy_images_fallback
```

### 19.2 Fidelity App Development Notes

The first controlled benchmark app should be a small native Android app, provisionally `VigilMarket`, stored under `fidelity_app/` at the repository root. It should be buildable from the command line and installable on the user's Pixel 6a emulator, currently visible as `emulator-5554`.

Recommended commands:

```bash
cd fidelity_app
./gradlew assembleDebug
./gradlew installDebug
adb -s emulator-5554 shell monkey -p com.vigil.market 1
```

Design goals:

1. Keep UI intentionally simple and deterministic: no network, login, database, external images, or unnecessary runtime permissions.
2. Cover Vigil's three error families with a compact shopping-style flow: home, search/catalog, product detail template, cart, address selection, payment confirmation, success, orders, settings, and confirmation dialogs.
3. Expose stable observability for UIAutomator/accessibility: visible labels, `contentDescription` for non-text controls, consistent Compose `testTag`s, and a stable screen marker such as `screen:home`.
4. Maintain hidden evaluator artifacts in `fidelity_app/gold/`, including `fsm.json`, `guards.json`, and `tasks.json`. These files are ground truth for evaluator comparison and must not be surfaced in the app UI.
5. Treat this app as a calibration target for `state_id`, canonical action identity `<tau,q,v>`, transition extraction, template abstraction, DSL guard evaluation, and replay confidence. Do not wire it into the Python pipeline until the app and gold artifacts are stable.

---

## 20. Risk Awareness

| Risk | Severity | Mitigation |
|------|----------|------------|
| State abstraction granularity wrong | High | Start with ActionEngine's atom-based approach, iterate |
| FSM replay pass rate low | High | Analyze failure causes (timing? non-determinism?) → retry + relaxed matching |
| Insufficient differentiation from ActionEngine/Agent-SAMA | Medium | Emphasize: offline vs online, verification vs planning, symbolic vs neuro |
| Value-level semantics incomplete | Medium | Paper positions structural verification as core, value-level as extension |
| State localization inaccurate | Medium-Low | Fingerprint + multi-feature similarity matching |
| WebView/mini-program poor Accessibility support | Low-Medium | Acknowledge scope limitation, focus on native UI |
| Google Play Accessibility policy risk | Low | Our system is deterministic rule-based, not autonomous agent |
| Taxonomy and implementation drift apart | Medium | Keep `docs/error_taxonomy.md`, Section 4.0 module mapping, and evaluation metrics aligned |
| Safety layer becomes a loose prompt policy | High | Express high-risk constraints as DSL guards/invariants whenever possible; use LLM fallback only after UNCERTAIN |
