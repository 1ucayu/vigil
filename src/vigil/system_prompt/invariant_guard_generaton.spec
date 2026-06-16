[PROMPT]
Generate typed invariant and guard candidates for an existing mobile-GUI FSM.

This prompt is for LLM-assisted semantic enrichment of an already-built Vigil FSM.
The FSM topology is fixed. The LLM may propose typed candidate facts and
contracts, but admission, DSL parsing, replay confidence, and runtime verdicts
remain symbolic / deterministic.

Use this file as a structured specification:
```text
[RELY]:
  Defines the inputs, evidence packet, verifier interface, and read-only facts.

[GUARANTEE]:
  Defines the field-level semantics of the output packet. The object shape itself is
  enforced by the provider's structured-output schema, not by this spec.

[SPECIFICATION]:
  Defines the preconditions, legal outcomes, always-enforced rules, and synthesis
  algorithm.

[SPECIFICATION of ...]:
  Defines refinement rules for specific subcontracts; these rules are normative.
```

If [RELY] provides evidence that cannot be used under [SPECIFICATION], follow
[SPECIFICATION] and return rejected or metadata-only candidates instead of using
that evidence unsafely.

The output object shape is fixed and enforced by the provider's structured-output schema; this
spec is a semantic policy, not the schema authority. Emit a single candidate packet object.

## Logical Model
This prompt follows a contract-first invariant/guard synthesis model. State
invariants are semantic safety contracts, not full UI snapshot summaries.

Use the following Hoare-style reading:
```text
{ I(s) /\ Gamma(s,a) } a { I(s') }

Gamma(s,a) approximates wp_a(I(s'))
```

`I(s)` describes stable semantic facts that should hold whenever the verifier
localizes to state `s`. `Gamma(s,a)` describes pre-action predicates over the
source screen, known action, and frozen `$intent.*` slots. Target-state facts may
motivate guards through the approximate weakest-precondition reading, but an
executable guard must be evaluated only on the source screen.

The LLM is a candidate generator, not a proof oracle. Generate typed candidates
over the allowed abstract domains and predicate vocabulary; the admission layer
will verify alias resolution, evidence support, preservation, DSL executability,
and candidate consistency.

## Primary Prompt
[RELY]
```text
FSM_SCOPE:
  model: M_A = <S, s0, Sigma, delta, Gamma, I, rho>
  fsm_already_constructed: true
  synthesis_model: contract-first semantic invariant admission
  llm_role: propose typed candidates only
  generated_objects:
    - I: state/action/side-effect invariant candidates over typed abstract domains
    - Gamma: transition guard candidates for existing transitions only
  executable_backend: Vigil DSL evaluated by DSLEvaluator
  runtime_verdict_source: symbolic verifier only

READ_ONLY_BOUNDARY:
  states_are_fixed: true
  actions_are_fixed: true
  transitions_are_fixed: true
  replay_confidence_is_fixed: true
  state_ids_are_fixed: true
  canonical_action_identity_is_fixed: true

LOGIC_MODEL:
  state_invariant_meaning:
    I(s) is the set of stable, runtime-evaluable semantic facts needed for state
    localization, action interpretation, intent binding, or state-consistency safety
    checks. It is not a dump of all observed UI properties.
  hoare_contract_view:
    for an existing transition t = (s, a, s'), candidate generation is guided by
    { I(s) /\ Gamma(s,a) } a { I(s') }
  wp_reading:
    target invariant facts may suggest source guard candidates, but only when
    those candidates can be expressed over source
    evidence, the known action, and declared intent slots.
  action_logic_reading:
    a guard for action a should make the relevant target obligation hold after
    a is executed, as far as replay-backed GUI evidence can support it.
  preservation_reading:
    a runtime state invariant should be supported by target-state observations
    and by high-trust incoming replay arrivals into that state.
  admission_style:
    generate candidates broadly, then expect deterministic admission to reject
    unsupported, volatile, unresolvable, or non-executable candidates.
```

```text
INVARIANT_GUARD_EVIDENCE_PACKET:

  [Target state]:
    state_id: string
    state_name?: string
    display_name?: string
    activity_name?: string
    window_name?: string
    container_type?: string
    template_id?: string
    raw_screen_ids: list<string>
    observation_count: integer
    existing_invariant_specs?: list<StateInvariant>

  [State observations]:
    role: only evidence for state-level invariant candidates
    observations: list<StateObservation>
    minimum_for_high_trust_invariant: integer
    note: repeated visits are stronger evidence than one screenshot

  [Invariant Abstract Domains]:
    role: restrict state invariant candidates to stable semantic domains instead
          of arbitrary natural-language facts.
    layers:
      I_struct:
        meaning: screen identity, activity/window/dialog boundary, container
                 type, stable chrome, and structural presence facts.
        examples: read(<stable_title_alias>, text) == "<stable_screen_label>",
                  count(<stable_container_alias>) >= 1
      I_bind:
        meaning: widget alias to semantic role/action role bindings needed to
                 interpret future actions or intent slots.
        examples: <value_field_alias> is value-input metadata,
                  <commit_control_alias> is commit/action metadata,
                  <summary_label_alias> binds a selected-entity summary,
                  <scope_label_alias> binds a permission/capability scope
        note: executable expressions must still use supported predicates such as
              read/value/contains/count; semantic role claims are metadata unless
              admission can compile them.
      I_value:
        meaning: coarse form, selection, status, error, and value-domain facts.
        examples: selected option is checked, error text absent, status label
                  present, field is editable, value domain is enum/numeric hint
      I_safe:
        meaning: safety/side-effect facts that explain why a guard or audit
                 invariant is needed.
        examples: destructive confirmation present, permission scope shown,
                  success receipt/status appears after commit
        note: side-effecting safety facts usually require a transition guard; a
              state invariant is only a consistency check.

  [Arrival-state widget registry]:
    entries: map<alias, WidgetRegistryEntry>
    meaning: only legal element aliases for executable state invariant predicates
    requirement:
      - every executable state invariant predicate must reference this registry
        or another runtime-resolvable alias explicitly present in the observation
      - prefer aliases backed by resource_id, stable content_description,
        stable semantic role, or template alias
      - avoid capture-local e_XXXX aliases unless no stable alias exists; such
        candidates are low-trust or rejected unless admission can resolve them

  [Incoming transitions]:
    transitions: list<TransitionEvidence>
    role: classify state-consistency and side-effect hints

  [Outgoing transitions]:
    transitions: list<TransitionEvidence>
    role: classify pre-action guard candidates and sibling choices

  [Global Information / Static APK Priors]:
    manifest_activity_labels?: list<string>
    permissions?: list<string>
    resource_strings?: map<string, string>
    string_arrays?: map<string, list<string>>
    layout_widget_declarations?: list<string>
    menu_navigation_resources?: list<string>
    role: semantic role/domain/value-domain/side-effect hints only; not runtime proof
    anti_leakage:
      - package names, app slugs, bundle names, raw screen ids, file paths, and
        benchmark/evaluator labels are provenance only
      - do not infer app domain, guard kind, slot names, or literal values from
        package names or fixture identifiers
      - if a package/static identifier looks benchmark-specific or hidden-task-like,
        ignore the identifier text except as provenance

  [Verifier Basis]:
    predicate_vocabulary: PredicateBasis
    readable_element_properties: set<Property>
    readable_action_properties: set<Property>
    output_schema: InvariantGuardCandidatePacket

  [Effect and Verification-Obligation Taxonomy]:
    role: classify why a guard or invariant is needed without hardcoding
          app-specific labels.
    dimensions:
      state_topology:
        meaning: current screen, modal/dialog boundary, tab/page, container shape
        usual_obligation: structural invariant only when stable and executable
      semantic_binding:
        meaning: intended item/person/account/address/file/value/content is selected,
                 shown, or about to be acted on
        usual_obligation: transition guard when pre-action source can prove intent;
                          metadata hint when only successor evidence shows it
      local_reversible_state:
        meaning: editable local state that can be corrected before final commit
        usual_obligation: guard or invariant depending on executable evidence
      irreversible_or_costly_state:
        meaning: destructive, paid, externally visible, security-sensitive,
                 privacy-sensitive, permission-granting, or hard to undo
        usual_obligation: semantic guard before commit; state invariant may
                          audit success/error state but cannot replace the guard
      external_side_effect:
        meaning: communication, publication, order placement, transfer, payment,
                 account/security change, deletion, permission grant, or device/app
                 state change outside the current screen
        usual_obligation: source guard plus effect/status invariant when executable
```

```text
StateObservation:
  screen_id: string
  xml_excerpt: string          // full XML text when available; field name is legacy
  compact_tree_text: string
  screenshot_image?: image
  alt_text: string
  xml_tree_path: string        // provenance only
  screenshot_path: string      // provenance only
  page_function?: string
  display_name?: string

TransitionEvidence:
  source_state_id: string
  target_state_id: string
  known_action: Action
  replay_confidence: float
  low_trust: bool
  resolved_source_widget_alias?: string
  source_guard_registry?: map<alias, WidgetRegistryEntry>
  source_summary?: string
  target_summary?: string
  source_to_target_diff?: string
  sibling_outgoing_actions?: list<Action>

Action:
  action_type: click | long_press | input_text | scroll_up | scroll_down |
               scroll | navigate_back | navigate_home | unknown
  target_text?: string
  target_resource_id?: string
  target_content_desc?: string
  input_text?: string
  target_selector?: object

WidgetRegistryEntry:
  alias: string
  resource_id?: string
  text?: string
  content_description?: string
  role?: button | text_field | toggle | checkbox | radio | list_container |
         list_item | title | menu_item | toolbar_action | dialog_action |
         semantic_label | value_label | destination_label | entity_label |
         source_label | item_label | content_preview | status_text |
         error_text | success_text | permission_scope | capability_scope | unknown
  semantic_role?: string
  readable_props: set<Property>
  provenance?: runtime | runtime+apk_prior | runtime+llm | runtime+apk_prior+llm
```

```text
PredicateBasis:
  read(element, property) <op> value
  value(element) <op> value
  action(property) <op> value
  count(element) <op> value
  in_state(state_name)
  time_in(start, end)

AllowedOperator:
  == | != | > | < | >= | <= | contains | not_contains

ReadableElementProperty:
  text | content_description | value | is_clickable | is_long_clickable |
  is_checkable | is_checked | is_enabled | is_editable | is_scrollable |
  is_focusable | is_focused | is_selected | is_password | class_name |
  resource_id | children | children_count | item_count

ReadableActionProperty:
  action_type | target_text | target_resource_id | target_content_desc | input_text
```

[GUARANTEE]
The output object shape (the `InvariantGuardCandidatePacket`) is fixed and enforced by the
provider's structured-output schema — do not restate or invent JSON shape here. This section
describes only the *meaning* of each field so candidates are well-formed semantically:

  * `state_invariant_candidates`: proposed state facts `I(s)`. Each has a single executable
    `expr` over the arrival registry, a semantic `kind`, an `admission_target`
    (`runtime_state_invariant` | `metadata_only` | `reject`), an evidence-based `confidence`
    and `evidence_count`, a `volatility` classification, `provenance`, `notes`, and an optional
    `rejection_reason`.
  * `transition_guard_candidates`: proposed pre-action guards `Gamma(s,a)` for existing
    transitions, addressed by `source_state_id`, `target_state_id`, and the verbatim
    `canonical_action_key`. Each carries a typed guard `contract` (kind, required,
    required_slots, predicates, binding_requirements, confidence, provenance, notes).
  * `effect_invariant_hints`: useful conditional/action/intent-dependent facts that are NOT
    runtime state invariants, each with a `why_not_runtime_state_invariant` reason.
  * `rejected_candidates`: candidates deliberately not emitted as executable, with reasons.
  * `notes`: free-form synthesis reasoning.

Slot names, expressions, and literals must be generic and evidence-derived. Use neutral
role-based placeholders (e.g. `<typed_value_slot>`, `<selected_entity_slot>`,
`<target_control_slot>`, `<scope_label_alias>`, `<success_status_literal>`,
`<source_summary_alias>`, `<option_label_alias>`, `<commit_summary_alias>`) rather than copying
any concrete app's strings. Never derive content from package names, app slugs, raw screen ids,
file paths, or evaluator/gold labels.

[SPECIFICATION]
**Given**:
  * The FSM already exists. The LLM is not constructing `S`, `Sigma`, `delta`, or
    `rho`.
  * State observations and registries are runtime evidence. Static APK artifacts
    are priors only.
  * Candidate generation follows the Hoare-style obligation:
    `{ I(s) /\ Gamma(s,a) } a { I(s') }`.
  * `I(s')` is the successor-state invariant set. `Gamma(s,a)` is the
    source-state guard candidate induced by the approximate weakest-precondition
    reading of `I(s')`.
  * `Gamma` candidates are pre-action guards over a source screen, a known action,
    and frozen `$intent.*` variables.
  * Runtime-admitted `I` candidates are state checks over the current screen. In
    the current implementation they are evaluated with `ScreenContext` only, so
    they must not require `$intent.*` or `action(...)`.
  * LLM candidates are not admitted verifier state. They are proposed contracts
    that must later pass deterministic admission checks.
  * `effect_invariant_hints` are metadata for future conditional/action-aware
    invariants. They must not be written into `AbstractState.invariant_specs` until
    a deterministic admission path can execute them.

**Legal Outcomes**:

**Case 1 (Runtime state invariant)**:
  If a candidate fact is stable, executable, and meaningful for the target state:
    * Emit one `state_invariant_candidates` item.
    * Set `admission_target = "runtime_state_invariant"`.
    * Put exactly one parseable DSL expression in `expr`.
    * Classify it as one of the invariant domains: structural/container,
      semantic binding/action affordance, value/status, or safety/effect audit.
    * Use only arrival-state registry aliases and element predicates executable
      with `ScreenContext`.
    * Set `confidence` according to evidence strength; multi-visit evidence is
      stronger than one observation.
    * Set `evidence_count` to the number of observations supporting the fact.

**Case 2 (Metadata-only invariant hint)**:
  If the fact is semantically useful but not currently executable as a state
  invariant:
    * Emit an `effect_invariant_hints` item or a state invariant candidate with
      `admission_target = "metadata_only"`.
    * Explain why it cannot be admitted now.
    * Do not claim it is runtime-enforceable.

**Case 3 (Transition guard candidate)**:
  If an existing outgoing or incoming transition requires a pre-action safety or
  semantic binding check:
    * Emit a `transition_guard_candidates` item using the `GuardContract` shape.
    * Derive the guard obligation from source evidence and, when useful, from the
      target-state invariant through the approximate wp reading.
    * The guard may reference only source guard registry aliases, known-action
      properties, literals supported by source/action evidence, and declared
      `$intent.*` slots.
    * `required_slots` is the candidate contract's declared intent interface for
      this transition. Declare a slot only when it is grounded in source/action
      evidence, or when an external task intent-slot interface is explicitly present
      in the input packet.
    * For input, row/item selection, option selection, form submission, and
      commit-like transitions, first try executable semantic binding predicates.
      A guard whose only executable predicate is enabledness/clickability is
      incomplete when source/action evidence can bind a declared intent slot.
    * Do not use target-only UI in executable guard predicates.
    * Do not modify the transition or canonical action identity.

**Case 4 (Reject)**:
  If a candidate depends on invented aliases, target-only evidence in a guard,
  volatile text, unsupported predicates, unbound intent/action context, or static
  proof alone:
    * Omit it from executable candidate lists.
    * Add a `rejected_candidates` item with an evidence-based reason.

**Always-Enforced Rules**:
  * No FSM state, transition, action, replay confidence, state id, canonical action
    key, or runtime verdict is created or changed.
  * The LLM output is an intermediate candidate packet, not admitted verifier state.
  * State invariants are semantic contracts for localization, binding, status, and
    safety; they are not complete UI summaries.
  * Generate candidates only inside the typed abstract domains in
    `[Invariant Abstract Domains]`.
  * Runtime state invariants are stored in `AbstractState.invariant_specs`.
  * Transition guards are pre-action checks in `Transition.guard` /
    `Transition.guard_contract`.
  * The approximate wp reading may justify why a guard is needed, but it does not
    permit target-only UI in executable guard predicates.
  * Incoming preservation evidence may support a runtime invariant, but the LLM must
    not claim a proof beyond the replay observations provided.
  * A state invariant and a transition guard are separate candidate types; do not
    use one to fabricate evidence for the other.
  * A guard never uses target-only UI as executable evidence.
  * A runtime state invariant never uses source-only UI from a predecessor state.
  * Static APK priors never prove current UI values, current element presence,
    transition existence, replay confidence, safety, or runtime verdicts.
  * Do not generate static-only edges, static-only guards, or static-only invariants.
  * Do not use volatile observations as invariants: clock/stopwatch/timer text,
    timestamps, loading animation frames, ads, random ids, message feed churn,
    dynamic row contents, balances that may update externally, or user-entered raw
    text unless the task explicitly says it is a stable summary.
  * Do not create exact count invariants for dynamic lists unless repeated evidence
    proves a stable structural count. Prefer range/shape facts only when executable
    and supported.
  * Do not create literal text invariants for user-specific entity names, item
    names, destinations, typed values, or authored content unless the text is a
    stable UI label or status.
  * Use intent binding in guards, not state invariants, when the fact depends on the
    user's requested value.
  * Every executable expression must parse under `PredicateBasis`.
  * The output object shape is enforced by the structured-output schema.

**System Algorithm**:
  1. Build the target-state evidence view from runtime observations, the
     arrival-state widget registry, existing invariants, and static priors.
  2. Identify stable facts inside `I_struct`, `I_bind`, `I_value`, and `I_safe`:
     screen identity, dialog/modal boundary, required labels, action affordances,
     enabled/checked/selected state, container shape, status/error/success facts,
     semantic role bindings, value domains, and safety-relevant summaries.
  3. Filter out volatile, user-specific, target-only-for-guard, predecessor-only,
     unsupported, and static-only facts.
  4. For each candidate invariant, check whether high-trust incoming transitions
     provide preservation evidence through their arrival observations.
  5. Convert executable state facts into one-predicate DSL expressions over the
     arrival-state widget registry; keep non-executable semantic facts as
     metadata-only or rejected candidates.
  6. Classify incoming/outgoing transitions by the Effect and
     Verification-Obligation Taxonomy.
  7. For transitions that need pre-action checking, derive `GuardContract`
     candidates from source evidence plus target invariant
     obligations under the approximate wp reading.
  8. For useful but currently non-executable conditional facts, produce
     `effect_invariant_hints` instead of executable invariants.
  9. Record rejected candidates with reasons.

## Refine Prompt
[SPECIFICATION of State Invariant Scope]
**Given**:
  * A candidate is considered for `state_invariant_candidates`.

**Requirement**:
  * The candidate describes a fact that should hold whenever the verifier localizes
    to the target state after arrival.
  * The candidate does not depend on which predecessor transition was taken unless
    it is marked `metadata_only`.
  * The candidate does not use `$intent.*`, `$bind.*`, or `action(...)` while the
    runtime invariant checker supplies only `ScreenContext`.
  * The candidate is not a restatement of the state id, raw screen id, file path, or
    screenshot path.
  * The candidate is useful for localization, semantic binding, value/status
    checking, or safety/effect auditing. Do not emit inert facts merely because
    they are present in the screenshot.

[SPECIFICATION of Contract Layers]
**Given**:
  * A state invariant candidate is assigned a semantic purpose.

**Requirement**:
  * Use `structural`, `container_shape`, or `stable_label` for `I_struct` facts:
    activity/window/dialog boundaries, stable chrome labels, and required
    structural presence/count facts.
  * Use `semantic_binding` or `action_affordance` for `I_bind` facts: widget
    aliases that identify roles such as entity, source, destination, item,
    content, option, scope, or typed-value fields and submit/cancel/confirm
    controls. If the role cannot be expressed in the executable DSL, emit it as
    metadata-only.
  * Use `form_status`, `selection_status`, `status`, `error_absence`,
    `success_presence`, or `value_domain` for `I_value` facts: coarse form state,
    enabledness, checked/selected state, visible error/success messages, or
    runtime-confirmed value domains.
  * Use `safety_summary` or `side_effect_audit` for `I_safe` facts: irreversible,
    costly, externally visible, privacy/security-sensitive, or permission-related
    UI facts. These facts explain candidate guard context; they do not by themselves
    authorize side-effecting actions.

[SPECIFICATION of Hoare / wp Coupling]
**Given**:
  * A target-state invariant or source-to-target diff suggests
    a guard candidate for an existing transition `(s, a, s')`.

**Requirement**:
  * Read the transition as:
    `{ I(s) /\ Gamma(s,a) } a { I(s') }`.
  * Use target facts only to infer what the source guard should protect, then
    express executable guard predicates over the source guard registry, known
    action properties, literals supported in the source, and declared `$intent.*`
    slots.
  * If the target fact depends on an intent value but the source screen does not
    expose a runtime-readable predicate that can check it, emit an
    `effect_invariant_hints` item or reject the guard candidate with a clear reason.
  * Do not treat the approximate wp reading as proof. It is a synthesis heuristic
    whose output must pass deterministic admission.

[SPECIFICATION of Incoming Preservation]
**Given**:
  * A candidate state invariant is evaluated against incoming transitions.

**Requirement**:
  * High-trust incoming replay arrivals into the target state should support the
    invariant on the arrival observation.
  * If an invariant holds only for one predecessor, action, or intent value, emit it
    as `effect_invariant_hints` rather than a runtime state invariant.
  * Low-trust incoming transitions may provide notes or side-effect hints, but they do not
    justify high-confidence runtime invariants.
  * Missing incoming evidence lowers confidence or moves the candidate to
    metadata-only; it does not authorize invention.

[SPECIFICATION of Guard Scope]
**Given**:
  * A candidate is considered for `transition_guard_candidates`.

**Requirement**:
  * The candidate describes `Gamma(source screen P, known_action, frozen $intent.*)`.
  * The candidate may use target evidence only for classification or notes.
  * The candidate should be linked in `notes` to the invariant or
    transition fact that induced it when such a link is available.
  * The candidate must follow the same executable-soundness rules as
    `transition_guard_generation_readable_registry.spec`.
  * Slot names must be generic and evidence-derived from widget roles, action
    properties, resource/text semantics, or runtime-confirmed static priors. Do not
    derive slots from benchmark-specific app knowledge, package names, hidden task
    answers, or fixture constants.
  * Package names, app slugs, bundle names, raw screen ids, file paths, and
    evaluator/gold labels are provenance only. Do not use them to infer the app
    domain, choose a guard kind, choose slot names, or introduce expected literals.
  * Before emitting only `read(..., is_enabled) == true` or
    `read(..., is_clickable) == true`, check whether the transition is an input,
    selection, option, form-submit, or commit-like action whose source/action
    evidence can express a semantic binding with `action(...)`, `read(...)`, or
    `value(...)` against a declared `$intent.*` slot.
  * Enabledness/clickability may remain as readiness predicates, but they do not
    replace semantic binding when executable binding evidence is available.

[SPECIFICATION of Effect Invariant Hints]
**Given**:
  * A fact is meaningful only after a specific transition, action, or intent value.

**Requirement**:
  * Emit it as `effect_invariant_hints`, not as a runtime state invariant.
  * Examples include "after a side-effecting commit, a stable status label appears",
    "after selecting an entity, a source/target summary reflects
    $intent.<selected_entity_slot>", and "after submitting a typed value, a stable
    summary reflects $intent.<typed_value_slot>".
  * If the fact is side-effecting or authority-changing, also produce or request a pre-action guard when source
    evidence supports one.

[SPECIFICATION of Executable DSL]
**Given**:
  * A candidate contains an executable expression or predicate list.

**Requirement**:
  * Use only `read`, `value`, `count`, `in_state`, `time_in`, and `action` as
    allowed by the candidate scope.
  * Express containment as the `contains` or `not_contains` operator over `read`
    or `value`, for example `value(title) contains "Done"`.
  * For runtime state invariants, allowed predicate variables are `read`, `value`,
    `count`, `in_state`, and `time_in`; avoid `time_in` unless a stable time policy
    is explicitly provided.
  * For transition guards, `action` is allowed only over known action properties.
  * Do not emit natural-language pseudo-predicates such as `visible(...)`,
    `selected(...)`, `matches(...)`, `is_target_entity(...)`, `has_error(...)`,
    or `screen_contains(...)`.
  * Do not emit compound expressions for state invariants unless each atom is
    independently executable and admission can parse the full DSL expression.

[SPECIFICATION of Stable Aliases]
**Given**:
  * A candidate references an element.

**Requirement**:
  * Prefer `resource_id` aliases from runtime evidence.
  * Use content description, stable semantic role, or template alias only when the
    admission layer can resolve it to a runtime element.
  * Capture-local `e_XXXX` aliases are low-trust and should be rejected unless the
    packet explicitly says they are stable across visits.
  * Never invent an alias.

[SPECIFICATION of Evidence Strength]
**Given**:
  * A confidence score is assigned to a candidate.

**Requirement**:
  * Multi-visit runtime evidence with the same resolved alias and property supports
    higher confidence.
  * One observation can support only low or medium confidence unless static priors
    corroborate that the fact is a stable app label and runtime evidence confirms
    the element is present.
  * Static priors may boost confidence for stable labels, resource roles, and value
    domains only after runtime evidence confirms the widget exists.
  * A screenshot-only visual impression without XML/registry support is annotation
    evidence, not executable invariant proof.
  * Confidence is evidence strength, not semantic importance. Side-effecting facts may
    be important while still having low confidence or no admitted executable predicate.

[SPECIFICATION of Candidate Admission Style]
**Given**:
  * The LLM is deciding whether to emit, downgrade, or reject a candidate.

**Requirement**:
  * Prefer over-generation as typed candidates plus explicit rejection reasons over
    silently omitting safety-relevant concerns.
  * A candidate is not admitted merely because it is plausible. Admission requires
    runtime evidence, stable alias resolution, executable vocabulary, volatility
    filtering, and scope-correct use of source/target evidence.
  * If a candidate is semantically useful but cannot be executed by the current
    checker, emit it as metadata-only or `effect_invariant_hints`.
  * If a candidate would mislead runtime verification, reject it.

[SPECIFICATION of Volatility]
**Given**:
  * A candidate reads text, value, count, selectedness, checkedness, or enabledness.

**Requirement**:
  * Mark volatile facts as rejected or metadata-only.
  * Exact literal text is acceptable for stable chrome labels, button labels,
    dialog titles, permission scopes, status labels, and error/success messages.
  * Exact literal text is not acceptable for user-entered fields, user-authored
    content, entity names, item names, computed totals, balances, dates, times,
    counters, and feed rows unless repeated evidence and domain context prove
    stability.
  * Treat fixture-looking names, synthetic app labels, benchmark personas/items, and
    task-answer strings as user-specific or hidden-test data unless runtime evidence
    shows they are stable UI chrome.
  * Boolean facts such as enabled/checked/selected may be invariants only when they
    are stable state facts, not transient interaction artifacts.

[SPECIFICATION of Side-Effecting Transition Guards]
**Given**:
  * Evidence suggests an outgoing transition commits an irreversible/costly or
    external side effect.

**Requirement**:
  * Prefer a transition guard candidate with executable source-side predicates when
    evidence supports one.
  * A success/status invariant may be emitted as a consistency check, but
    it does not prove the pre-action guard.
  * If source evidence cannot support an executable guard, omit the guard candidate
    or return it with a clear `rejection_reason`; do not mark it as mandatory.

[SPECIFICATION of Invalid Output]
**Given**:
  * A candidate would require unsupported vocabulary, invented aliases, target-only
    evidence in a guard, predecessor-only evidence in a state invariant, static-only
    proof, volatile facts, undeclared intent slots, or runtime contexts that the
    checker does not supply.

**Requirement**:
  * Omit the invalid executable candidate.
  * If the idea is still useful for future work, move it to `effect_invariant_hints`
    with `why_not_runtime_state_invariant`.
  * Otherwise add it to `rejected_candidates`.
