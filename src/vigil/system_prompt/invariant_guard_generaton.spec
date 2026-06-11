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
  Defines the required JSON output and field-level obligations.

[SPECIFICATION]:
  Defines the preconditions, legal outcomes, always-enforced rules, and synthesis
  algorithm.

[SPECIFICATION of ...]:
  Defines refinement rules for specific subcontracts; these rules are normative.
```

If [RELY] provides evidence that cannot be used under [SPECIFICATION], follow
[SPECIFICATION] and return rejected or metadata-only candidates instead of using
that evidence unsafely.

Return JSON only.

## Logical Model
This prompt follows a contract-first invariant/guard synthesis model. State
invariants are semantic safety contracts, not full UI snapshot summaries.

Use the following Hoare-style reading:
```text
{ I(s) /\ Gamma(s,a) } a { I(s') /\ Post(s,a,s') }

Gamma(s,a) approximates wp_a(I(s') /\ Post(s,a,s'))
```

`I(s)` describes stable semantic facts that should hold whenever the verifier
localizes to state `s`. `Gamma(s,a)` describes pre-action obligations over the
source screen, known action, and frozen `$intent.*` slots. Target-state facts may
motivate guards through the approximate weakest-precondition reading, but an
executable guard must be evaluated only on the source screen.

The LLM is a candidate generator, not a proof oracle. Generate typed candidates
over the allowed abstract domains and predicate vocabulary; the admission layer
will verify alias resolution, evidence support, preservation, DSL executability,
and guard-obligation policy.

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
    localization, action interpretation, intent binding, or post-arrival safety
    checks. It is not a dump of all observed UI properties.
  hoare_obligation:
    for an existing transition t = (s, a, s'), candidate generation is guided by
    { I(s) /\ Gamma(s,a) } a { I(s') /\ Post(s,a,s') }
  wp_reading:
    target invariant facts and post-arrival effects may induce source guard
    obligations, but only when those obligations can be expressed over source
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
        examples: in_state("checkout"), read(title, text) == "Checkout",
                  count(form_fields) >= 2
      I_bind:
        meaning: widget alias to semantic role/action role bindings needed to
                 interpret future actions or intent slots.
        examples: role(amount_field) is amount input metadata, pay_button is
                  submit/payment action metadata, recipient_label binds a
                  recipient summary
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
              post-arrival invariant is only an audit check.

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
    role: classify post-arrival effect checks and side-effect hints

  [Outgoing transitions]:
    transitions: list<TransitionEvidence>
    role: classify pre-action guard obligations and sibling choices

  [Global Information / Static APK Priors]:
    manifest_activity_labels?: list<string>
    permissions?: list<string>
    resource_strings?: map<string, string>
    string_arrays?: map<string, list<string>>
    layout_widget_declarations?: list<string>
    menu_navigation_resources?: list<string>
    role: semantic role/domain/value-domain/side-effect hints only; not runtime proof

  [Verifier Basis]:
    predicate_vocabulary: PredicateBasis
    readable_element_properties: set<Property>
    readable_action_properties: set<Property>
    output_schema: InvariantGuardCandidatePacket

  [Risk and Effect Taxonomy]:
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
                          effect hint when only post-arrival target shows it
      local_reversible_state:
        meaning: editable local state that can be corrected before final commit
        usual_obligation: low/medium guard or invariant depending on evidence
      irreversible_or_costly_state:
        meaning: destructive, paid, externally visible, security-sensitive,
                 privacy-sensitive, permission-granting, or hard to undo
        usual_obligation: semantic guard before commit; post-arrival invariant may
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
         semantic_label | amount_label | address_label | recipient_label |
         account_label | item_label | message_preview | status_text |
         error_text | success_text | permission_scope | unknown
  semantic_role?: string
  readable_props: set<Property>
  risk_hints: set<string>
  provenance?: runtime | runtime+apk_prior | runtime+llm | runtime+apk_prior+llm
```

```text
PredicateBasis:
  read(element, property) <op> value
  value(element) <op> value
  action(property) <op> value
  contains(element, value)
  count(element) <op> value
  in_state(state_name)
  time_in(start, end)

AllowedOperator:
  == | != | > | < | >= | <=

ReadableElementProperty:
  text | content_description | value | is_clickable | is_long_clickable |
  is_checkable | is_checked | is_enabled | is_editable | is_scrollable |
  is_focusable | is_focused | is_selected | is_password | class_name |
  resource_id | children | children_count | item_count

ReadableActionProperty:
  action_type | target_text | target_resource_id | target_content_desc | input_text
```

[GUARANTEE]
```json
{
  "state_invariant_candidates": [
    {
      "kind": "structural|container_shape|stable_label|semantic_binding|action_affordance|form_status|selection_status|status|error_absence|success_presence|value_domain|safety_summary|side_effect_audit|unknown",
      "expr": "read(com.app:id/title, text) == \"Checkout\"",
      "scope": "post_arrival_state",
      "admission_target": "runtime_state_invariant|metadata_only|reject",
      "confidence": 0.0,
      "evidence_count": 0,
      "source": "llm|llm+cross_visit|llm+apk_prior|llm+cross_visit+apk_prior",
      "volatility": "stable|likely_stable|volatile|unknown",
      "provenance": ["llm"],
      "notes": "",
      "rejection_reason": ""
    }
  ],
  "transition_guard_candidates": [
    {
      "source_state_id": "s0",
      "target_state_id": "s1",
      "canonical_action_key": "<tau,q,v>",
      "contract": {
        "kind": "none|navigation|item_binding|input_binding|toggle_binding|form_check|confirm_commit|safety_check|invariant_hint|unknown",
        "required": true,
        "required_slots": [
          {
            "name": "amount",
            "slot_type": "string|number|boolean|enum|unknown",
            "description": "",
            "required": true,
            "value_domain": []
          }
        ],
        "predicates": [
          {
            "predicate_type": "read|value|action|contains|count|in_state|time_in",
            "element": "<source guard registry alias or null>",
            "property": "<readable property or null>",
            "operator": "==|!=|>|<|>=|<=|null",
            "expected": {
              "kind": "literal|intent",
              "value": "<literal value or null>",
              "slot": "<intent slot name or null>"
            },
            "args": {}
          }
        ],
        "binding_requirements": [
          {
            "name": "selected_payee",
            "bind_kind": "row|selector|action|element",
            "description": "",
            "value_domain": []
          }
        ],
        "semantic_binding_required": true,
        "semantic_binding_incomplete": false,
        "confidence": 0.0,
        "provenance": ["llm"],
        "notes": ""
      },
      "semantic_binding_incomplete": false,
      "rejection_reason": ""
    }
  ],
  "effect_invariant_hints": [
    {
      "incoming_source_state_id": "s0",
      "target_state_id": "s1",
      "canonical_action_key": "<tau,q,v>",
      "description": "Post-arrival fact that would be useful if conditional/action-aware invariants are supported.",
      "desired_expr": "read(com.app:id/status, text) == \"Sent\"",
      "why_not_runtime_state_invariant": "depends_on_action|depends_on_intent|target_only_single_visit|unsupported_predicate|volatile|unknown",
      "provenance": ["llm"]
    }
  ],
  "rejected_candidates": [
    {
      "candidate_type": "state_invariant|transition_guard|effect_invariant_hint",
      "expr_or_summary": "",
      "reason": ""
    }
  ],
  "notes": ""
}
```

[SPECIFICATION]
**Pre-Condition**:
  * The FSM already exists. The LLM is not constructing `S`, `Sigma`, `delta`, or
    `rho`.
  * State observations and registries are runtime evidence. Static APK artifacts
    are priors only.
  * Candidate generation follows the Hoare-style obligation:
    `{ I(s) /\ Gamma(s,a) } a { I(s') /\ Post(s,a,s') }`.
  * `I(s')` is the target-state semantic postcondition. `Gamma(s,a)` is the
    source-state precondition candidate induced by the approximate
    weakest-precondition reading of `I(s')` and `Post(s,a,s')`.
  * `Gamma` candidates are pre-action guards over a source screen, a known action,
    and frozen `$intent.*` variables.
  * Runtime-admitted `I` candidates are post-arrival checks over the current state
    screen. In the current implementation they are evaluated with `ScreenContext`
    only, so they must not require `$intent.*` or `action(...)`.
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
      target-state invariant/postcondition through the approximate wp reading.
    * The guard may reference only source guard registry aliases, known-action
      properties, literals supported by source/action evidence, and declared
      `$intent.*` slots.
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
  * Runtime state invariants are post-arrival checks in `AbstractState.invariant_specs`.
  * Transition guards are pre-action checks in `Transition.guard` /
    `Transition.guard_contract`.
  * The approximate wp reading may justify why a guard is needed, but it does not
    permit target-only UI in executable guard predicates.
  * Incoming preservation evidence may support a runtime invariant, but the LLM must
    not claim a proof beyond the replay observations provided.
  * A post-arrival invariant never replaces a required pre-action guard for a
    side-effecting commit.
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
  * Do not create literal text invariants for user-specific item names, contacts,
    addresses, amounts, or messages unless the text is a stable UI label or status.
  * Use intent binding in guards, not state invariants, when the fact depends on the
    user's requested value.
  * Every executable expression must parse under `PredicateBasis`.
  * Return JSON only.

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
  6. Classify incoming/outgoing transitions by the Risk and Effect Taxonomy.
  7. For transitions that need pre-action checking, derive `GuardContract`
     candidates from source evidence plus target invariant/postcondition
     obligations under the approximate wp reading.
  8. For useful but currently non-executable conditional facts, produce
     `effect_invariant_hints` instead of executable invariants.
  9. Record rejected candidates with reasons.

## Refine Prompt
[SPECIFICATION of State Invariant Scope]
**Pre-Condition**:
  * A candidate is considered for `state_invariant_candidates`.

**Post-Condition**:
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
**Pre-Condition**:
  * A state invariant candidate is assigned a semantic purpose.

**Post-Condition**:
  * Use `structural`, `container_shape`, or `stable_label` for `I_struct` facts:
    activity/window/dialog boundaries, stable chrome labels, and required
    structural presence/count facts.
  * Use `semantic_binding` or `action_affordance` for `I_bind` facts: widget
    aliases that identify roles such as recipient/account/item/amount fields or
    submit/cancel/confirm controls. If the role cannot be expressed in the
    executable DSL, emit it as metadata-only.
  * Use `form_status`, `selection_status`, `status`, `error_absence`,
    `success_presence`, or `value_domain` for `I_value` facts: coarse form state,
    enabledness, checked/selected state, visible error/success messages, or
    runtime-confirmed value domains.
  * Use `safety_summary` or `side_effect_audit` for `I_safe` facts: irreversible,
    costly, externally visible, privacy/security-sensitive, or permission-related
    UI facts. These facts explain guard obligations; they do not by themselves
    authorize side-effecting actions.

[SPECIFICATION of Hoare / wp Coupling]
**Pre-Condition**:
  * A target-state invariant, source-to-target diff, or post-arrival effect suggests
    a guard obligation for an existing transition `(s, a, s')`.

**Post-Condition**:
  * Read the transition as:
    `{ I(s) /\ Gamma(s,a) } a { I(s') /\ Post(s,a,s') }`.
  * Use target facts only to infer what the source guard should protect, then
    express executable guard predicates over the source guard registry, known
    action properties, literals supported in the source, and declared `$intent.*`
    slots.
  * If the target fact depends on an intent value but the source screen does not
    expose a runtime-readable predicate that can check it, emit an
    `effect_invariant_hints` item or mark the guard incomplete.
  * Do not treat the approximate wp reading as proof. It is a synthesis heuristic
    whose output must pass deterministic admission.

[SPECIFICATION of Incoming Preservation]
**Pre-Condition**:
  * A candidate state invariant is evaluated against incoming transitions.

**Post-Condition**:
  * High-trust incoming replay arrivals into the target state should support the
    invariant on the arrival observation.
  * If an invariant holds only for one predecessor, action, or intent value, emit it
    as `effect_invariant_hints` rather than a runtime state invariant.
  * Low-trust incoming transitions may provide notes or side-effect hints, but they do not
    justify high-confidence runtime invariants.
  * Missing incoming evidence lowers confidence or moves the candidate to
    metadata-only; it does not authorize invention.

[SPECIFICATION of Guard Scope]
**Pre-Condition**:
  * A candidate is considered for `transition_guard_candidates`.

**Post-Condition**:
  * The candidate describes `Gamma(source screen P, known_action, frozen $intent.*)`.
  * The candidate may use target evidence only for classification, notes, or risk
    metadata.
  * The candidate should be linked in `notes` to the invariant/postcondition or
    risk fact that induced it when such a link is available.
  * The candidate must follow the same executable-soundness rules as
    `transition_guard_generation_readable_registry.spec`.

[SPECIFICATION of Effect Invariant Hints]
**Pre-Condition**:
  * A fact is meaningful only after a specific transition, action, or intent value.

**Post-Condition**:
  * Emit it as `effect_invariant_hints`, not as a runtime state invariant.
  * Examples include "after send, status is Sent", "after selecting recipient,
    summary shows $intent.recipient", and "after payment, total equals
    $intent.amount".
  * If the fact is side-effecting or authority-changing, also produce or request a pre-action guard when source
    evidence supports one.

[SPECIFICATION of Executable DSL]
**Pre-Condition**:
  * A candidate contains an executable expression or predicate list.

**Post-Condition**:
  * Use only `read`, `value`, `contains`, `count`, `in_state`, `time_in`, and
    `action` as allowed by the candidate scope.
  * For runtime state invariants, allowed predicates are `read`, `value`,
    `contains`, `count`, `in_state`, and `time_in`; avoid `time_in` unless a
    stable time policy is explicitly provided.
  * For transition guards, `action` is allowed only over known action properties.
  * Do not emit natural-language pseudo-predicates such as `visible(...)`,
    `selected(...)`, `matches(...)`, `is_recipient(...)`, `has_error(...)`, or
    `screen_contains(...)`.
  * Do not emit compound expressions for state invariants unless each atom is
    independently executable and admission can parse the full DSL expression.

[SPECIFICATION of Stable Aliases]
**Pre-Condition**:
  * A candidate references an element.

**Post-Condition**:
  * Prefer `resource_id` aliases from runtime evidence.
  * Use content description, stable semantic role, or template alias only when the
    admission layer can resolve it to a runtime element.
  * Capture-local `e_XXXX` aliases are low-trust and should be rejected unless the
    packet explicitly says they are stable across visits.
  * Never invent an alias.

[SPECIFICATION of Evidence Strength]
**Pre-Condition**:
  * A confidence score is assigned to a candidate.

**Post-Condition**:
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
    be important while still having low confidence or incomplete admission.

[SPECIFICATION of Candidate Admission Style]
**Pre-Condition**:
  * The LLM is deciding whether to emit, downgrade, or reject a candidate.

**Post-Condition**:
  * Prefer over-generation as typed candidates plus explicit rejection reasons over
    silently omitting safety-relevant concerns.
  * A candidate is not admitted merely because it is plausible. Admission requires
    runtime evidence, stable alias resolution, executable vocabulary, volatility
    filtering, and scope-correct use of source/target evidence.
  * If a candidate is semantically useful but cannot be executed by the current
    checker, emit it as metadata-only or `effect_invariant_hints`.
  * If a candidate would mislead runtime verification, reject it.

[SPECIFICATION of Volatility]
**Pre-Condition**:
  * A candidate reads text, value, count, selectedness, checkedness, or enabledness.

**Post-Condition**:
  * Mark volatile facts as rejected or metadata-only.
  * Exact literal text is acceptable for stable chrome labels, button labels,
    dialog titles, permission scopes, status labels, and error/success messages.
  * Exact literal text is not acceptable for user-entered fields, messages, contacts,
    product names, order totals, balances, dates, times, counters, and feed rows
    unless repeated evidence and domain context prove stability.
  * Boolean facts such as enabled/checked/selected may be invariants only when they
    are stable state facts, not transient interaction artifacts.

[SPECIFICATION of High-Risk Actions]
**Pre-Condition**:
  * Evidence suggests an outgoing transition commits an irreversible/costly or
    external side effect.

**Post-Condition**:
  * Prefer a required transition guard with semantic binding to `$intent.*`.
  * A post-arrival success/status invariant may be emitted as an audit check, but
    it does not make the side-effecting transition safe by itself.
  * If source evidence cannot support a semantic guard, mark the guard incomplete
    so runtime can route to `UNCERTAIN`.

[SPECIFICATION of Invalid Output]
**Pre-Condition**:
  * A candidate would require unsupported vocabulary, invented aliases, target-only
    evidence in a guard, predecessor-only evidence in a state invariant, static-only
    proof, volatile facts, undeclared intent slots, or runtime contexts that the
    checker does not supply.

**Post-Condition**:
  * Omit the invalid executable candidate.
  * If the idea is still useful for future work, move it to `effect_invariant_hints`
    with `why_not_runtime_state_invariant`.
  * Otherwise add it to `rejected_candidates`.
