# Neuro-Symbolic Architecture Survey and Justification

This reference file backs the compact implementation skeleton in `AGENTS.md` and `CLAUDE.md` Sections 4.1 and 4.2. Keep the root files short; use this document for the literature grounding, technical challenges, and symbolic definitions that justify Vigil's design.

## 1. Verifier Artifact

For an Android app `A`, Vigil constructs a verifier artifact:

```text
M_A = <S, s0, Sigma, delta, Gamma, I, rho>
```

where:

| Symbol | Meaning | Implementation Anchor |
|--------|---------|-----------------------|
| `S` | Abstract UI states | `AbstractState`, `AppFSM.states` |
| `s0` | Initial abstract state | `AppFSM.initial_state` |
| `Sigma` | Canonical GUI actions | `models.action`, `core.action_types` |
| `delta` | Action-labeled transition relation `delta subseteq S x Sigma x S` | `AppFSM.graph`, `Transition` |
| `Gamma` | Transition guard map `S x Sigma -> guard` | `Transition.guard`, `models.dsl` |
| `I` | State/action/side-effect invariant map `S x Sigma -> 2^Phi` | `AbstractState.state_invariants`, `invariant_checker.py` |
| `rho` | Replay confidence map `delta -> [0, 1]` | `Transition.confidence` |

A runtime screen observation `o_t` is localized by:

```text
alpha(o_t) -> (s_t, p_loc)
```

where `s_t` is the matched abstract state and `p_loc` is the localization confidence. The current implementation uses exact structural fingerprints first and then structural similarity for evolution candidates.

The core acceptance rule is:

```text
ALLOW iff (s_t, a_t, s') in delta
      and Reach(s', goal)
      and rho(s_t, a_t, s') >= theta_conf
      and eval(Gamma(s_t, a_t), o_t, intent, a_t) = true
      and forall phi in I(s', a_t): eval(phi, o_t, intent, a_t) = true
```

`DENY` means Vigil proves a transition, reachability, guard, or invariant violation. `UNCERTAIN` means Vigil cannot prove safety because localization, replay confidence, intent binding, template trust, or predicate evaluation is incomplete.

## 2. Offline Stage Survey

### Survey Reading Notes

The following notes are the paper-grounding layer for the stage designs below:

| Area | Read/Checked Work | Relevant Finding | Vigil Takeaway |
|------|-------------------|------------------|----------------|
| UI-guided exploration | DroidBot | Builds UI transition graphs with lightweight, UI-guided input generation and no app instrumentation requirement. | Vigil should treat Accessibility/uiautomator traces as first-class evidence and preserve enough UI metadata to reconstruct transitions. |
| Search-based mobile testing | Sapienz | Uses multi-objective search to optimize Android test sequences for coverage, fault revelation, and shorter fault-revealing traces. | Exploration policy should be bounded and goal-aware; raw BFS alone is unlikely to hit high-value semantic paths. |
| Stochastic model-based testing | Stoat | Reverse-engineers a stochastic GUI model using dynamic/static analysis, then mutates/refines the model to guide test generation. | The learned GUI model is useful, but Vigil must convert it into a verifier with confidence gates instead of only a test generator. |
| Model abstraction/refinement | APE | Dynamically optimizes GUI abstraction during testing; explicitly targets imprecision in static GUI models. | Vigil's state abstraction must be evolvable and replay-validated because one fixed fingerprint policy will be brittle. |
| UI datasets and semantics | Rico, Android in the Wild | UI traces combine screenshots, actions, and natural language tasks; AITW emphasizes multi-step tasks and visual/semantic action inference. | State identity cannot be purely structural; guard generation needs screen purpose and intent-slot semantics. |
| UI representation learning | UIBert, Ferret-UI, SeeClick, OmniParser | UI understanding benefits from image, text, layout, structural metadata, grounding, and widget-level reasoning. | Vigil should keep screenshot + XML + semantic aliases, because no single representation is enough for guard synthesis. |
| Agent runtime enforcement | AgentSpec, GuardAgent, VeriSafe Agent | Runtime safety policies are useful when expressed as executable rules/DSLs, but many systems rely on manually defined or LLM-mediated policies. | Vigil's DSL must be grammar-checked, tied to app topology, and kept out of the common runtime LLM path. |
| FSM-based GUI agents | SPlanner, Agent-SAMA, ActionEngine, V-Droid | Recent GUI agents use FSMs or verifiers for planning, recovery, or action selection. | Vigil must be positioned as an external runtime verifier, not another planner or acting GUI agent. |
| Adaptive guardrails | AGrail, MAGNET/HyMEM-style memory, EvoFSM-style workflow adaptation | Adaptive systems improve through memory/check refinement but usually keep neural components in the runtime loop. | Vigil's Tier 3 should evolve verifier artifacts asynchronously while preserving symbolic trust gates. |

Open caveat: several 2025-2026 agent papers are fast-moving preprints or newly accepted conference papers. Before paper submission, re-check venue metadata, final titles, and claims; do not rely on cached arXiv/project-page wording for final related-work tables.

### Stage/Tier Coverage Audit

This table is the traceability check: every Vigil stage or runtime tier has at least one directly checked paper/source claim and a concrete design consequence.

| Vigil Component | Sources Checked | Source Claim Used | Design Consequence |
|-----------------|-----------------|-------------------|--------------------|
| Stage 1: UI exploration | [DroidBot](https://ylimit.github.io/static/files/DroidBot_ICSE2017.pdf), [Sapienz](https://discovery.ucl.ac.uk/id/eprint/1508043/), [Stoat](https://tingsu.github.io/files/fse17-stoat.pdf), [APE](https://gutianxiao.com/static/ape-icse-2019.pdf) | DroidBot builds an on-the-fly UI transition model without app instrumentation; Sapienz optimizes test sequences for coverage/fault revelation/length; Stoat constructs a stochastic FSM with weighted exploration; APE dynamically refines GUI model abstraction. | Exploration must capture rich traces, support more than random/BFS, and preserve replayable `(screen, action, next_screen)` evidence. |
| Stage 2: abstraction and grounding | [Rico](https://experts.illinois.edu/en/publications/rico-a-mobile-app-dataset-for-building-data-driven-design-applica/), [Android in the Wild](https://arxiv.org/abs/2307.10088), [SeeClick](https://arxiv.org/abs/2401.10935), [OmniParser](https://arxiv.org/abs/2408.00203) | Rico exposes visual, textual, structural, and interactive properties of 72k+ mobile screens; AITW includes screens, actions, instructions, and multi-step visual/semantic context; SeeClick identifies GUI grounding as a key bottleneck; OmniParser parses interactable regions and functional semantics. | State abstraction must combine structural fingerprints with semantic profiles, icon labels, visual context, and intent-slot grounding. |
| Stage 3: hierarchical FSM construction | [Stoat](https://tingsu.github.io/files/fse17-stoat.pdf), [APE](https://gutianxiao.com/static/ape-icse-2019.pdf), [Angluin L*](https://www.sciencedirect.com/science/article/pii/0890540187900526), [Learned Cloud Emulators](https://conferences.sigcomm.org/hotnets/2025/papers/hotnets25-final207.pdf) | Stoat and APE both rely on GUI models/FSM-style abstractions; Angluin motivates finite automata learning from queries/counterexamples; Learned Cloud Emulators models resources as hierarchical state machines with constrained generation. | Build a compact hierarchical FSM, keep transition provenance, and use dynamic templates to avoid enumerating every repeated item. |
| Stage 4: DSL guard generation | [VeriSafe Agent](https://arxiv.org/abs/2503.18492), [AgentSpec](https://arxiv.org/abs/2503.18666), [GuardAgent](https://arxiv.org/abs/2406.09187), [Learned Cloud Emulators](https://conferences.sigcomm.org/hotnets/2025/papers/hotnets25-final207.pdf) | VeriSafe formalizes user instructions into verifiable specifications for pre-action checking; AgentSpec uses a lightweight DSL with triggers/predicates/enforcement; GuardAgent compiles guard requests into executable checks; Learned Cloud Emulators constrains generated artifacts with formal abstractions and syntactic checks. | Generated guards must be grammar-checked, executable, linked to app topology, and admitted only after variable/element resolution succeeds. |
| Stage 5: replay verification | [APE](https://gutianxiao.com/static/ape-icse-2019.pdf), [Stoat](https://tingsu.github.io/files/fse17-stoat.pdf), [Daikon](https://homes.cs.washington.edu/~mernst/pubs/daikon-tool-scp2007-abstract.html), [runtime verification finite-trace semantics](https://www.pspace.org/a/publications/JLC2010.pdf) | APE emphasizes replayable high-level model actions and model refinement; Stoat uses models to generate diverse event sequences; Daikon reports likely invariants from observed executions; finite-trace runtime verification distinguishes true/false/inconclusive observations. | Store replay confidence, mine only likely invariants, and map unproven transitions to `UNCERTAIN` rather than treating one observation as proof. |
| Tier 1: structural FSM verification | [Runtime verification survey](https://www.isp.uni-luebeck.de/research/publications/brief-account-runtime-verification), [APE](https://gutianxiao.com/static/ape-icse-2019.pdf), [Stoat](https://tingsu.github.io/files/fse17-stoat.pdf) | Runtime verification checks executions against formal properties at runtime; Android model-based testing shows GUI behavior can be represented as finite-state transitions. | The runtime checker should be a deterministic monitor over `alpha(o_t)`, `(s_t,a_t,s') in delta`, reachability, and confidence. |
| Tier 2: guard/invariant verification | [VeriSafe Agent](https://arxiv.org/abs/2503.18492), [AgentSpec](https://arxiv.org/abs/2503.18666), [Daikon](https://homes.cs.washington.edu/~mernst/pubs/daikon-tool-scp2007-abstract.html), [RV-LTL finite-trace semantics](https://www.pspace.org/a/publications/JLC2010.pdf) | Logic/DSL-based guards can check actions before execution; dynamic invariant detection provides likely constraints; finite traces may be inconclusive. | Predicate evaluation must be three-valued: `T`, `F`, or `U`; `F` gives `DENY`, while `U` gives `UNCERTAIN`. |
| Tier 3: template inheritance and micro-evolution | [APE](https://gutianxiao.com/static/ape-icse-2019.pdf), [AGrail](https://aclanthology.org/2025.acl-long.399/), [MAGNET](https://arxiv.org/abs/2601.19199), [HyMEM](https://arxiv.org/abs/2603.10291), [EvoFSM](https://arxiv.org/abs/2601.09465) | APE dynamically refines GUI abstraction; AGrail adapts safety checks over time; MAGNET/HyMEM evolve structured memory for GUI agents; EvoFSM evolves explicit finite-state workflows to avoid unconstrained prompt/code drift. | Vigil should evolve the verifier artifact, not the acting policy; inherited states start low-trust and require replay promotion before high-confidence `ALLOW`. |

### Stage 1: UI Exploration

**Technical challenge.** Android GUI exploration is not a clean graph crawl. The action space grows with every clickable, editable, scrollable, checkable, and system-level element; the same action may lead to different screens depending on permission dialogs, network state, account state, animation timing, or scroll position. Naive random exploration under-samples semantically important states, while exhaustive exploration is infeasible.

**Relevant literature.** DroidBot showed that UI-guided input generation can improve Android exploration by using the visible UI structure rather than blind random events. Sapienz framed Android testing as a multi-objective search problem that optimizes for coverage and fault revelation. Stoat and APE-style work use model-based abstraction/refinement to keep exploration from exploding while still adapting to newly observed behavior. These systems mostly target testing, crashes, or coverage; Vigil reuses the exploration insight but changes the artifact being built: a runtime verifier rather than a test suite. See [DroidBot](https://github.com/honeynet/droidbot), [Sapienz](https://dl.acm.org/doi/10.1145/2970276.2970336), and [APE](https://swag.uwaterloo.ca/publications/practical-gui-testing-of-android-applications-via-model-abstraction-and-refinement.html).

**Vigil design.** Stage 1 records a trace multiset:

```text
Tau = {(o_i, a_i, o_j, meta_i)}
```

where each observation keeps the accessibility tree, screenshot, interactable element set, action metadata, device/app context, and timestamps. This trace is intentionally richer than a normal testing trace because later stages need evidence for abstraction, guard generation, replay, and confidence estimation.

### Stage 2: State Abstraction and Semantic Grounding

**Technical challenge.** Raw screens are too fine-grained: two restaurant detail pages, WiFi lists, or chat threads may contain different text but share the same functional UI state. At the same time, visually similar pages may have different safety semantics, such as confirm-payment versus confirm-message. The core risk is choosing the wrong abstraction granularity: over-splitting causes state explosion; over-merging creates unsound verification.

**Relevant literature.** Rico, Android in the Wild, Screen2Words, Widget Captioning, UIBert, SeeClick, OmniParser, and Ferret-UI show that UI understanding needs both structure and semantics: widget roles, screen purpose, icon meaning, text grounding, and action affordance matter. They motivate Vigil's separation between structural fingerprints for cacheable topology and semantic profiles for guard generation. See [Rico](https://experts.illinois.edu/en/publications/rico-a-mobile-app-dataset-for-building-data-driven-design-applica/), [Android in the Wild](https://arxiv.org/abs/2307.10088), [SeeClick](https://arxiv.org/abs/2401.10935), [OmniParser](https://arxiv.org/abs/2408.00203), and [Ferret-UI](https://arxiv.org/abs/2404.05719).

**Vigil design.** Vigil uses two abstractions:

```text
phi_struct(o) = hash({(class, resource_id, depth, role, interactability)})
phi_sem(o)    = (page_function, expected_actions, icon_labels, invariants)
```

`phi_struct` is optimized for deterministic runtime localization. `phi_sem` is optimized for human-meaningful guard generation. A state merge is safe only when structural similarity is high and semantic conflict is not observed:

```text
merge(o_i, o_j) iff sim(phi_struct(o_i), phi_struct(o_j)) >= tau_s
                   and conflict(phi_sem(o_i), phi_sem(o_j)) = false
```

### Stage 3: Hierarchical FSM Construction

**Technical challenge.** A flat mobile UI graph can explode because a repeated fragment may appear inside many activities, scrollable list items may generate unbounded detail states, and dialogs may overlay many parent states. The FSM must be small enough for runtime verification but precise enough to reject illegal actions.

**Relevant literature.** Classical model learning, especially Angluin's L* algorithm, provides the conceptual pattern of learning finite automata through membership and equivalence-style evidence. Android model-based testing systems refine GUI abstractions as new counterexamples appear. The Learned Cloud Emulators methodology is especially relevant: it turns unstructured domain behavior into hierarchical state machines and then uses constrained generation and symbolic checking. Vigil adapts the same style from APIs/cloud objects to mobile GUI states. See [Angluin L*](https://dl.acm.org/doi/10.1016/0890-5401%2887%2990052-6) and the related learned-emulator methodology discussed in the root project context.

**Vigil design.** Build a hierarchical transition system rather than a flat graph:

```text
H = (S_app, S_activity, S_fragment, S_component, parent)
delta_h: S x Sigma -> S
```

Dynamic repeated regions are represented through templates:

```text
Tmpl_k = (S_k, delta_k, Params_k, bind_k)
```

This lets a single detail-page template represent many runtime items while preserving the ability to bind item-specific content at Tier 2.

### Stage 4: DSL Guard Generation

**Technical challenge.** FSM topology answers whether an action is structurally legal, but not whether it is semantically correct. A click on `Send` may be legal from a chat screen while still targeting the wrong recipient. A click on `Pay` may be legal while violating amount or merchant constraints. Guard generation must therefore be expressive enough for semantic binding and safety constraints, but restricted enough to parse, evaluate, and audit deterministically.

**Relevant literature.** VeriSafe Agent and AgentSpec-style systems show the value of rule or DSL-based enforcement for agents, but they rely heavily on manually specified or externally authored constraints. Constrained decoding and grammar validation motivate generating only DSL-valid guards. Vigil's novelty is tying those guards to an automatically constructed per-app FSM and replay confidence instead of treating them as standalone policies. See the formal guardrail direction in [VeriSafe Agent](https://arxiv.org/abs/2503.18492).

**Vigil design.** A transition guard is a predicate over the current screen, action, state, time, and frozen intent:

```text
gamma_{s,a}(o_t, intent, a_t) in {true, false, unknown}
```

Generation is accepted only if:

```text
parse_lark(gamma) succeeds
and free_intent_vars(gamma) subseteq extracted_intent_schema
and referenced_elements(gamma) subseteq semantic_aliases(s)
```

This converts LLM output into a checked symbolic artifact before runtime use.

### Stage 5: Replay Verification and Confidence Scoring

**Technical challenge.** A transition observed once may not be reliable. It may depend on timing, a particular account state, a network result, or a hidden modal. Verification needs a way to separate "seen once" from "trusted enough for runtime allow."

**Relevant literature.** Model-based Android testing uses replay and refinement to validate or correct learned GUI models. Daikon-style dynamic invariant detection motivates mining likely invariants from observed executions and then treating them as confidence-weighted hypotheses rather than absolute truth until validated. Runtime verification literature motivates monitors that can return inconclusive results when a finite trace does not prove a property. See [APE](https://swag.uwaterloo.ca/publications/practical-gui-testing-of-android-applications-via-model-abstraction-and-refinement.html), [Daikon](https://plse.cs.washington.edu/daikon/pubs/daikon-tool-scp2007-abstract.html), and [runtime verification background](https://dl.acm.org/doi/10.1007/978-3-642-16612-9_1).

**Vigil design.** Replay estimates transition confidence:

```text
rho(s, a, s') = success_count(s, a, s') / trial_count(s, a, s')
```

For bounded path set `P_L = {p | len(p) <= L}`, replay validates each path against expected localized states:

```text
pass(p) iff forall (s_i, a_i, s_{i+1}) in p:
             alpha(exec(o_i, a_i)).state = s_{i+1}
```

Low confidence does not delete the transition. It lowers trust, causing the runtime to return `UNCERTAIN` instead of `ALLOW`.

## 3. Online Tier Survey

### Tier 1: Structural FSM Verification

**Technical challenge.** The runtime must decide whether the agent is on the expected screen and whether the proposed action exists in the verified topology. This must happen fast enough to sit before every GUI action.

**Relevant literature.** Finite-state monitors and runtime verification treat program execution as a stream of events checked against a formal model. Android GUI testing provides the learned topology, but Vigil changes the use case from offline testing to online enforcement.

**Vigil design.** Tier 1 uses the partial transition relation:

```text
delta subseteq S x Sigma x S
```

The structural verdict is:

```text
D1(o_t, a_t, goal) =
  UNCERTAIN, if alpha(o_t) = unknown
  DENY,      if no s' satisfies (s_t, a_t, s') in delta
  DENY,      if goal != null and not Reach(s', goal)
  UNCERTAIN, if rho(s_t, a_t, s') < theta_conf
  ALLOW,     otherwise
```

This tier covers wrong-screen, illegal-action, dead-end, loop, and low-confidence topology failures.

### Tier 2: Parameterized Guard and Invariant Verification

**Technical challenge.** Structurally legal actions can still bind the wrong runtime value. The verifier needs to freeze the user's intent, map intent slots into screen predicates, and reject actions that are legal in the graph but wrong for the task.

**Relevant literature.** UI-grounding work motivates the need for semantic binding, while DSL guardrail work motivates deterministic enforcement. Three-valued runtime semantics are important because a missing field, absent binding, or parse failure is not the same as proof of safety.

**Vigil design.** DSL evaluation returns three values:

```text
eval(g, o_t, intent, a_t) in {T, F, U}
```

where `U` means unknown due to missing element, missing intent variable, invalid type conversion, or inaccessible UI content. Tier 2 verdict composition is:

```text
D2 = DENY      if any guard/invariant evaluates F
     UNCERTAIN if any required guard/invariant evaluates U
     ALLOW     if all required guards/invariants evaluate T
```

This tier covers wrong-field, wrong-value, wrong-contact, wrong-item, and unsafe side-effect errors.

### Tier 3: Template Inheritance and Micro-Evolution

**Technical challenge.** Dynamic apps cannot be fully enumerated offline. The runtime will encounter new restaurants, products, contacts, chats, and list items. The key question is whether these are truly novel states or parameterized instances of a known structural template.

**Relevant literature.** Adaptive agent-safety and memory-evolution work such as AGrail, MAGNET/HyMEM-style graph memory, and EvoFSM-style workflow evolution motivate systems that improve after deployment. Vigil differs by evolving the verifier artifact under symbolic trust gates instead of directly evolving the acting agent's policy.

**Vigil design.** For an unseen observation, compute structural similarity against known states:

```text
J(o, s) = |C(o) intersect C(s)| / |C(o) union C(s)|
```

where `C(.)` is the set of structural components such as `(class_name, resource_id, depth)`. If `max_s J(o, s) >= tau_inherit`, Vigil can create a new low-trust state by inheriting the template:

```text
s_new = inherit_and_bind(s_template, params(o))
rho(s_new, a, s') = min(rho(s_template, a, s'), theta_low)
```

If no similar state exists, the runtime returns `UNCERTAIN` and queues asynchronous micro-evolution. The new state can improve future coverage, but it should not receive high-trust `ALLOW` status until replay verification raises its confidence.

## 4. Design Consequences

1. **Exploration is evidence collection, not the final model.** Stage 1 should preserve enough raw evidence for later abstraction, guard generation, and replay.
2. **State identity is two-layered.** Structural fingerprints support fast runtime localization; semantic profiles support guard generation and human audit.
3. **The FSM is a verifier, not a planner.** Planning systems use FSMs to choose actions; Vigil uses the FSM to reject proposed actions that cannot be proven safe.
4. **DSL guards are admitted only after parsing.** LLM-generated text never becomes runtime policy unless the grammar accepts it and referenced variables/elements are resolvable.
5. **Replay confidence gates trust.** A transition can exist in the graph but still produce `UNCERTAIN` until repeated replay makes it trustworthy.
6. **Evolution increases coverage, not immediate trust.** Tier 3 states enter the artifact as low-confidence hypotheses and must be promoted through replay.

## 5. Reference Clusters

| Cluster | Representative Work | Why It Matters For Vigil |
|---------|---------------------|--------------------------|
| Android GUI exploration/testing | DroidBot, Sapienz, Stoat, APE | Shows how to discover GUI states and refine models under action explosion. |
| UI datasets and grounding | Rico, Android in the Wild, UIBert, Screen2Words, Widget Captioning, SeeClick, OmniParser, Ferret-UI | Shows why structural trees need semantic grounding for widget/action meaning. |
| Automata learning and model checking | Angluin L*, finite-state runtime verification, learned emulator methods | Justifies representing app behavior as a learned transition system checked symbolically. |
| DSL and agent guardrails | VeriSafe Agent, AgentSpec, GuardAgent, VeriGuard | Motivates deterministic guards but exposes the limitation of manual or standalone rule sets. |
| Adaptive/evolving agents | AGrail, MAGNET/HyMEM, EvoFSM | Motivates post-deployment adaptation; Vigil applies adaptation to the verifier model rather than the agent policy. |

## 6. Implementation Mapping

| Research Need | Vigil Module |
|---------------|--------------|
| Capture raw GUI evidence | `neuro/explorer.py`, `neuro/ape_explorer.py`, `core/ui_parser.py` |
| Build stable state identity | `models/state.py`, `neuro/state_abstractor.py`, `neuro/semantic_grounder.py` |
| Maintain hierarchical verifier graph | `models/fsm.py`, `neuro/fsm_builder.py` |
| Generate and validate semantic guards | `neuro/dsl_generator.py`, `docs/dsl_grammar.lark`, `symbolic/dsl_evaluator.py` |
| Replay and confidence estimation | `neuro/replay_verifier.py`, `symbolic/trajectory_verifier.py` |
| Runtime structural checks | `symbolic/state_locator.py`, `symbolic/fsm_checker.py` |
| Runtime verdict composition | `symbolic/decision_engine.py`, `symbolic/invariant_checker.py` |
| Template inheritance and evolution | `neuro/evolution.py`, `models.fsm.SubFsmTemplate` |
