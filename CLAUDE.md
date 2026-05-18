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

Vigil is a **neuro-symbolic runtime verification system** for mobile GUI agents. Its paper narrative is organized around three mobile GUI error families: **GUI state and transition errors** (wrong screen, illegal action, dead end, loop), **GUI semantic binding errors** (wrong field, value, item, contact, address, or intent slot), and **GUI safety and side-effect errors** (structurally legal actions that violate user constraints or cause harmful irreversible effects). In the **offline (neuro) phase**, an LLM systematically explores a target Android app, abstracts raw screens into states, constructs a **per-app hierarchical Finite State Machine (FSM)**, and annotates transitions with **DSL semantic guards**. The FSM is verified by test-case generation and on-device replay. In the **online (symbolic) phase**, a lightweight engine checks every proposed GUI action before execution using FSM structure, DSL guards, task-state progress, invariants, and confidence thresholds, returning **ALLOW / DENY / UNCERTAIN** without a runtime LLM in the common path. Vigil is also **self-evolving**: unseen but structurally similar UI states inherit parameterized templates, while truly novel states trigger asynchronous micro-evolution and are cached back into the FSM bundle after validation.

---

## 3. Core Research Insight

Every mobile app's UI is essentially a **finite state machine** — screens are states, user actions are transitions. This FSM can be **automatically constructed** (neuro) and used for **formal verification** (symbolic). Even highly dynamic apps (UberEats, Taobao) have **static structural skeletons** — "different restaurant pages" share the same structural state template. Vigil separates **structure** (cacheable, formally verifiable) from **content** (runtime-bound via parameterized guards).

### Neuro-Symbolic Division of Labor

```
Neuro (Offline)                        Symbolic (Online)
──────────────                         ─────────────────
LLM-driven UI exploration        →     FSM graph construction
LLM-assisted state abstraction   →     Invariant mining (Daikon-style)
LLM-generated DSL guards         →     Model checking (formal verification)
                                       Symbolic execution (path analysis)
                                       Test case generation (FSM correctness proof)
                                       Predicate evaluation (guard checks)
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
| `I` | Invariant map from state-action pairs to sets of DSL formulas, `I:S x Sigma -> 2^Phi`. Invariants encode state, action, and side-effect constraints. | `AbstractState.state_invariants`, `InvariantChecker` |
| `rho` | Replay confidence map, `rho:delta -> [0,1]`. Low-confidence edges route to `UNCERTAIN`, not high-trust `ALLOW`. | `Transition.confidence`, `FsmChecker` |

Writing rule: describe `M_A` as a **DSL-guarded, confidence-annotated EFSM** built on the transition-system view underlying Kripke structures. A full Kripke structure additionally materializes atomic propositions `AP` and a labeling function `L:S -> 2^AP`; Vigil instead evaluates DSL predicates at runtime as transition contracts. Each verified transition may be read as a Hoare-style contract:

```text
{ Gamma(s, a) } a { I(s', a) }
```

This contract interpretation is the paper bridge between FSM topology, DSL semantic binding, and safety invariants.

---

## 4. System Architecture

### 4.0 Error-Family-to-Module Mapping

| Error Family | Primary Modules | Offline Support | Online Enforcement |
|--------------|-----------------|-----------------|--------------------|
| GUI State and Transition Errors | `models/fsm.py`, `models/action.py`, `core/ui_parser.py`, `core/action_types.py`, `symbolic/state_locator.py`, `symbolic/fsm_checker.py`, `symbolic/trajectory_verifier.py` | `neuro/explorer.py`, `neuro/state_abstractor.py`, `neuro/fsm_builder.py`, `neuro/replay_verifier.py` build and validate the topology. | Locate current state, check legal transition, reject unreachable paths, detect loops, return UNCERTAIN on low-confidence transitions. |
| GUI Semantic Binding Errors | `models/dsl.py`, `models/state.py`, `symbolic/dsl_evaluator.py`, `symbolic/intent_extractor.py`, `symbolic/trajectory_verifier.py`, `symbolic/decision_engine.py` | `neuro/semantic_grounder.py`, `neuro/dsl_generator.py`, `neuro/widget_templates.py`, `neuro/evolution.py` create semantic profiles, guards, and dynamic templates. | Freeze intent, bind `$intent.*` variables, evaluate guards, track multi-step task progress, inherit and bind templates for dynamic content. |
| GUI Safety and Side-Effect Errors | `symbolic/decision_engine.py`, `symbolic/invariant_checker.py`, `symbolic/dsl_evaluator.py`, `symbolic/fsm_checker.py`, `integration/agent_runner.py`, `scripts/verify_action.py` | Replay verification and guard generation identify high-risk transitions, irreversible actions, and state invariants. | Enforce safety guards and invariants before execution; return DENY or UNCERTAIN for risky, under-specified, or low-confidence actions. |

### 4.1 Offline Pipeline (Neuro Layer — 5 Stages)

Keep the root implementation skeleton concise. The deeper literature survey, design justification, and formal definitions live in `docs/references/neuro_symbolic_architecture_survey.md`.

**Stage 1: UI Exploration** (`vigil.neuro.explorer`, `vigil.neuro.ape_explorer`, `core.ui_parser`, `core.action_types`)
- Technical challenge: Android apps expose huge action spaces, nondeterministic transitions, scroll-dependent widgets, system dialogs, and state aliases caused by dynamic content.
- Implementation role: enumerate candidate actions from accessibility attributes, execute bounded BFS/DFS or APE-backed exploration, capture `(screen_before, action, screen_after)` triples, and preserve screenshots/XML/action metadata for later replay.
- Artifact: raw observation set `O`, candidate action alphabet `Sigma`, trace multiset `Tau`, and low-level transition samples saved under `data/apps/<app_name>/`.

**Stage 2: State Abstraction + Semantic Grounding** (`vigil.neuro.state_abstractor`, `vigil.neuro.semantic_grounder`, `models.state`)
- Technical challenge: exact screenshots over-split dynamic pages, while coarse fingerprints can merge semantically different states such as payment confirmation and message confirmation.
- Implementation role: compute structural fingerprints from stable UI skeleton features, attach semantic profiles from screenshots/accessibility trees, label icon-only widgets, and mine state invariants from repeated observations.
- Artifact: abstract states `S`, localization fingerprints, semantic aliases, state invariants, and static/dynamic container labels.

**Stage 3: Hierarchical FSM Construction** (`vigil.neuro.fsm_builder`, `models.fsm`)
- Technical challenge: flat GUI graphs explode because repeated fragments, list items, dialogs, and nested activities create many near-duplicate paths.
- Implementation role: build a hierarchy `App > Activity > Fragment > Component`, deduplicate raw screens into `AbstractState` nodes, attach transitions to `networkx.DiGraph`, and represent repeated dynamic item flows with `SubFsmTemplate` instead of enumerating every item.
- Artifact: per-app `AppFSM = (S, s0, Sigma, delta)` plus hierarchy metadata, transition provenance, and dynamic sub-FSM templates.

**Stage 4: DSL Guard Generation** (`vigil.neuro.dsl_generator`, `vigil.neuro.widget_templates`, `models.dsl`)
- Technical challenge: topology alone cannot prove semantic correctness; the verifier must know which recipient, amount, field, contact, item, or constraint the action binds.
- Implementation role: generate grammar-valid DSL predicates from semantic profiles, widget templates, platform priors, and task intent variables; parse every guard with `docs/dsl_grammar.lark` before admitting it to the bundle.
- Artifact: transition guard map `Gamma: S x Sigma -> guard`, required `$intent.*` bindings, guard provenance, and high-risk action labels.

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

**"A Case for Learned Cloud Emulators"** (HotNets'25, UMich + HKU + Berkeley) — methodologically isomorphic:
- Both: unstructured knowledge → formal state machine → constrained generation → symbolic verification
- Three inspirations: (1) hierarchical SM (VPC > Subnet > VM ≈ App > Activity > Fragment), (2) formal grammar for constrained generation, (3) automated alignment via symbolic execution
- Their domain: cloud APIs. Our domain: mobile GUI. Same methodology.

**Angluin's L\* Algorithm** (spirit) — our FSM construction is conceptually a modernized L\* with LLM as the "teacher" and UI exploration as membership queries.

**HyMEM** (arXiv'26) — hybrid symbolic + continuous graph representation; graph evolution via node add/update/replace. Directly relevant to our Tier 3 evolution mechanism.

**Pro2Guard** (arXiv'25) — DTMC from traces + probabilistic model checking. Potentially complementary: our FSM topology + their transition probabilities = Probabilistic FSM.

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
│       │   ├── state_abstractor.py # Stage 2: State Abstraction (fingerprint)
│       │   ├── semantic_grounder.py# Stage 2.5: Semantic Grounding (multimodal LLM)
│       │   ├── fsm_builder.py      # Stage 3: Hierarchical FSM Construction
│       │   ├── dsl_generator.py    # Stage 4: DSL Semantic Guard Generation
│       │   ├── widget_templates.py # Widget guard template lookup (from YAML)
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
│       │   ├── fsm.py             # AppFSM class (networkx DiGraph wrapper)
│       │   ├── dsl.py             # DSL guard data structures
│       │   ├── state.py           # AbstractState, RawScreen definitions
│       │   ├── action.py          # Action type definitions & templates
│       │   └── schemas/           # JSON schemas for FSM/DSL bundles
│       │       ├── fsm_schema.json
│       │       └── dsl_schema.json
│       │
│       ├── core/                   # Shared utilities
│       │   ├── __init__.py
│       │   ├── ui_parser.py       # Accessibility tree XML → structured repr
│       │   ├── action_types.py    # Action templates & enums
│       │   ├── screenshot.py      # Screenshot capture & annotation
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
│   ├── nsdi_paper_outline.md      # Compact NSDI-style paper outline
│   └── dsl_grammar.lark           # Formal grammar for DSL guards
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
from pydantic import BaseModel, Field
from enum import StrEnum

class HierarchyLevel(StrEnum):
    APP = "app"
    ACTIVITY = "activity"
    FRAGMENT = "fragment"
    COMPONENT = "component"

class ContainerType(StrEnum):
    STATIC = "static"
    DYNAMIC = "dynamic"
    NONE = "none"

class StateSemanticProfile(BaseModel):
    alt_text: str = ""
    page_function: str = ""
    expected_actions: list[str] = Field(default_factory=list)
    icon_labels: dict[str, str] = Field(default_factory=dict)
    generation_confidence: float = 0.0

class AbstractState(BaseModel):
    state_id: str
    name: str
    fingerprint: str                              # functional (FsmBuilder dedup)
    structural_fingerprint: str | None = None      # structural (online matching)
    hierarchy_level: HierarchyLevel
    parent_state: str | None = None
    activity_name: str | None = None
    invariants: list[str] = Field(default_factory=list)
    raw_screens: list[str] = Field(default_factory=list)
    container_type: ContainerType = ContainerType.NONE
    container_resource_id: str | None = None
    semantic_profile: StateSemanticProfile | None = None
    state_invariants: list[str] = Field(default_factory=list)
    invariant_confidence: float = 0.0
    sub_fsm_template_id: str | None = None

class Transition(BaseModel):
    source: str
    target: str
    action: dict[str, Any]                         # {"type": "click", "target": ..., "target_text": ...}
    guard: str | None = None                       # DSL guard expression
    confidence: float = 0.0                        # 1.0=observed, 0.5=inferred
    observed_count: int = 0

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
    def is_valid_transition(self, from_state: str, action: dict) -> bool: ...
    def is_reachable(self, from_state: str, goal_state: str) -> bool: ...
    def serialize(self, path: str | Path): ...
    @classmethod
    def deserialize(cls, path: str | Path) -> 'AppFSM': ...
```

### DSL Guard Grammar (`docs/dsl_grammar.lark`)

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
8. `docs/dsl_grammar.lark` — from §12
9. `tests/conftest.py` — shared fixtures
10. Run: `uv venv .venv --python 3.11 && uv pip install -e ".[dev]" && pre-commit install`

---

## 19. Implementation Priority & Development Notes

1. **Start with Settings app** — deterministic, no login, no network dependency, ideal for debugging.
2. **Build in the order of the taxonomy** — first block state/transition errors, then semantic binding errors, then safety/side-effect errors.
3. **Keep `decision_engine.py` as the single runtime verdict point** — every check must collapse into ALLOW / DENY / UNCERTAIN.
4. **`src/` layout is mandatory** — all code under `src/vigil/`, never import from repo root.
5. **Core novelty = per-app FSM+DSL verifier + self-evolution** — don't over-engineer Android infrastructure early.
6. **Keep symbolic verifier in pure Python first** — graph lookups + predicate eval are fast enough; only port to Kotlin/C++ if profiling justifies it.
7. **Verifier is agent-agnostic** — wraps ANY GUI agent as safety layer, does not replace agent.
8. **Replay non-determinism is expected** — use confidence scores, don't chase 100% reliability.
9. **State explosion mitigation**: hierarchy + bounded exploration (max 500 steps per app initially).
10. **Never commit `data/` or `models/bundles/`** — large generated artifacts.
11. **All LLM calls are offline only** — runtime symbolic layer must NEVER call an LLM in the common path; Tier 3 is async and infrequent.
12. **uv is the only package manager** — no pip, no requirements.txt.
13. **Reference code to borrow**:
    - V-Droid (`html_representation.py`): UI parsing, element filtering, display_id assignment → adapt for `core/ui_parser.py`
    - V-Droid (action enumeration): element properties → candidate actions → adapt for `core/action_types.py`
    - VeriSafe (predicate patterns): per-app guard templates (payment, messaging, shopping) → inspiration for `neuro/dsl_generator.py`
    - VeriSafe (ADB + screenshot + tree capture): simpler UI capture pipeline → reference for `neuro/explorer.py`

### 19.1 Current Implementation Alignment (May 2026)

The current prototype already represents most of the paper model: `S`, `s0`, `delta`, `Gamma`, and `rho` appear in `AppFSM` / `Transition`; `Sigma` is represented by `ActionType` and action dictionaries; DSL guards are generated and evaluated; transition confidence is checked in `FsmChecker`; state invariants are stored on `AbstractState` and checked by `InvariantChecker`.

Highest-priority alignment patches before the implementation section freezes:

1. **Canonical action identity:** `AppFSM.is_valid_transition`, `get_transition_target`, and `get_transition` currently match mostly on `action["type"]`. They should compare the canonical action signature `<tau, q, v>` so different widgets or values are not collapsed into the same transition.
2. **Three-valued DSL evaluation:** `DSLEvaluator` currently exposes a boolean `passed` result. Missing GUI elements, missing intent variables, parse failures, or unreadable predicates should produce `UNCERTAIN` rather than being treated as ordinary semantic violations.
3. **Invariant integration:** `InvariantChecker` exists, but `DecisionEngine` should evaluate the relevant successor/state/action invariants before returning high-trust `ALLOW`.
4. **Replay verifier completion:** `neuro/replay_verifier.py` is still a Stage-5 skeleton. It should update `Transition.confidence` from replay trials and preserve low-confidence transitions for `UNCERTAIN` handling.
5. **Low-trust evolution:** `FsmEvolver` currently copies template transition confidence when inheriting similar states. Inherited or micro-evolved edges should start with reduced confidence until replay validation promotes them.

Latest local validation snapshot for the aligned prototype subset:

```bash
uv run pytest tests/test_models.py tests/test_fsm_checker.py tests/test_decision_engine.py tests/test_invariant_checker.py tests/test_dsl_evaluator.py tests/test_fsm_builder.py tests/test_evolution.py
# 166 passed, 3 skipped
```

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
