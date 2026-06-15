[PROMPT]
Generate one typed transition contract for one existing mobile-GUI FSM transition:
an explicit source-side precondition `Gamma` and a target-side transition-effect
postcondition `Psi`.

Use this file as a structured specification:
```text
[RELY]:
  Defines the inputs, evidence packet, verifier interface, and read-only facts.

[GUARANTEE]:
  Defines the required output contract and field-level obligations.

[SPECIFICATION]:
  Defines the preconditions, legal outcomes, always-enforced rules, and synthesis algorithm.

[SPECIFICATION of ...]:
  Defines refinement rules for specific subcontracts; these rules are normative.
```

If [RELY] provides evidence that cannot be used under [SPECIFICATION], follow
[SPECIFICATION] and return a partial or rejected contract rather than using that
evidence unsafely.

The transition is presented as Hoare-style read-only evidence:
```text
{ Gamma(source screen P, known_action properties, frozen $intent.*) }
known_action
{ Psi(source, known_action, target, frozen $intent.*) AND I(target state Q) }
```

`known_action` is fixed by the FSM. Synthesize `Gamma` as the pre-action
transition guard and `Psi` as the edge-local postcondition contract. The
state-invariant layer `I` provides reusable consistency facts for target state Q.
`Psi` records the post-action facts selected for this fixed edge: target arrival
confirmation, target values induced by the action or frozen intent, and
source-to-target effect observations. `$bind.*` is recorded separately in binding
requirements; it is not an executable predicate in either `Gamma` or `Psi`.

Return JSON only.

First derive the contract from [RELY], [GUARANTEE], and [SPECIFICATION].
Then apply every section under ## Refine Prompt as additional constraints.
When rules overlap, keep the stricter executable-soundness constraint.

## Primary Prompt
[RELY]
```text
FSM_SCOPE:
  model: M_A = <S, s0, Sigma, delta, Gamma, I, rho>
  transition_exists_in_delta: true
  generated_object: Gamma and Psi
  generated_object_kind: typed transition pre/post contract candidate
  executable_backend:
    Gamma: conjunction of admitted source/action DSL predicates
    Psi: conjunction of admitted target-side and effect DSL predicates
  target_consistency_layer:
    I: reusable state-level invariants over Q, checked after transition arrival
  transition_effect_layer:
    Psi: edge-local postcondition facts for this specific transition

TRANSITION_SCOPE:
  source_state_id: string
  target_state_id: string
  known_action: Action
  replay_confidence: float
  low_trust: bool
```

```text
HOARE_TRANSITION_EVIDENCE_PACKET:

  [Transition]:
    source_state_id: string
    target_state_id: string
    source_state_name?: string
    target_state_name?: string
    source_screen_ids: list<string>
    target_screen_ids: list<string>
    replay_confidence: float
    low_trust: bool

  [Known action]:
    action: Action
    resolved_source_widget_alias?: string
    alias_resolution_reason?: string

  [Pre-state Evidence: P / source]:
    role: only executable UI read scope
    state_id: string
    screen_id: string
    xml_excerpt: string       // full XML text when available; field name is legacy
    compact_tree_text: string
    screenshot_image?: image
    alt_text: string
    xml_tree_path: string       // provenance only
    screenshot_path: string     // provenance only
    page_function?: string
    display_name?: string

  [Source widget registry]:
    entries: map<alias, WidgetRegistryEntry>
    meaning: only legal element aliases for executable element predicates

  [Post-state Evidence: Q / target]:
    role: executable postcondition read scope for transition-effect Psi;
          background evidence for Gamma
    state_id: string
    screen_id: string
    xml_excerpt: string       // full XML text when available; field name is legacy
    compact_tree_text: string
    screenshot_image?: image
    alt_text: string
    xml_tree_path: string       // provenance only
    screenshot_path: string     // provenance only
    page_function?: string
    display_name?: string

  [Source-to-target semantic/evidence diff]:
    diff_summary: string
    role: semantic disambiguation for both guard obligation and postcondition effects

  [Sibling outgoing actions]:
    actions: list<Action>
    role: distinguish choices, forms, commits, navigation, cancel/confirm, repeated rows

  [Global Information / Static APK Priors]:
    manifest_activity_labels?: list<string>
    permissions?: list<string>
    resource_strings?: map<string, string>
    string_arrays?: map<string, list<string>>
    layout_widget_declarations?: list<string>
    role: role/domain/postcondition hints only; not runtime proof

  [Verifier Basis]:
    predicate_vocabulary: PredicateBasis
    readable_element_properties: set<Property>
    readable_action_properties: set<Property>
    output_schema: TransitionPrePostContract

  [Action Impact Taxonomy]:
    role: classify why a guard is needed without hardcoding app-specific labels.
          Guard obligations come from required and semantic_binding_required,
          not from a severity label.
    dimensions:
      state_topology:
        meaning: screen navigation, modal open/close, back/cancel, tab change
        usual_obligation: usually optional unless it commits state or hides a destructive step
      semantic_binding:
        meaning: choosing the intended item/person/account/address/file/row/value
        usual_obligation: required when wrong binding changes task meaning
      local_reversible_state:
        meaning: editable local app state that can be corrected before final commit
        usual_obligation: required when it affects later commit; usually optional when purely cosmetic
      irreversible_or_costly_state:
        meaning: changes that are hard to undo, destructive, paid, externally visible,
                 security-sensitive, privacy-sensitive, or permission/authority granting
        usual_obligation: required
      external_side_effect:
        meaning: communication, publication, order placement, transfer, payment,
                 account/security change, data deletion, permission grant, or device/app
                 state change outside the current screen
        usual_obligation: required unless evidence proves the effect is reversible and local
    classification_factors:
      - source UI role and source widget registry facts
      - known_action properties and sibling alternatives
      - source->target semantic/evidence diff
      - target Q as postcondition/effect evidence
      - static APK priors as hints only
      - whether the user intent must bind item/value/recipient/account/address/content
```

```text
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
         image_button | unknown
  readable_props: set<Property>
```

```text
PredicateBasis:
  read(element, property) <op> value
  value(element) <op> value
  action(property) <op> value
  count(element) <op> value
  in_state(state_name)        // args.state in GuardContract JSON
  time_in(start, end)         // args.start and args.end in GuardContract JSON

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
```json
{
  "precondition": {
    "kind": "none|navigation|item_binding|input_binding|toggle_binding|form_check|confirm_commit|safety_check|unknown",
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
        "element": "<source registry alias or null>",
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
  "postcondition": {
    "kind": "none|arrival_state|content_effect|item_added|item_removed|message_sent|payment_or_transfer|toggle_effect|form_effect|unknown",
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
        "predicate_type": "read|value|count|in_state|time_in",
        "element": "<target registry alias, target resource id, or null>",
        "property": "<readable target property or null>",
        "operator": "==|!=|>|<|>=|<=|contains|not_contains|null",
        "expected": {
          "kind": "literal|intent",
          "value": "<literal value or null>",
          "slot": "<intent slot name or null>"
        },
        "args": {}
      }
    ],
    "effect_requirements": [
      {
        "name": "message_visible_after_send",
        "effect_kind": "audit_only|unknown",
        "description": "",
        "element": "<optional source or target registry alias/resource id, audit-only>",
        "before": {
          "kind": "literal|intent",
          "value": "<literal before value or null>",
          "slot": "<intent slot name or null>"
        },
        "after": {
          "kind": "literal|intent",
          "value": "<literal after value or null>",
          "slot": "<intent slot name or null>"
        },
        "evidence": "",
        "unsupported_reason": ""
      }
    ],
    "intent_effect_required": true,
    "intent_effect_incomplete": false,
    "confidence": 0.0,
    "provenance": ["llm"],
    "notes": ""
  },
  "contract": {
    "kind": "none|navigation|item_binding|input_binding|toggle_binding|form_check|confirm_commit|safety_check|unknown",
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
        "element": "<source registry alias or null>",
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
  "postcondition_incomplete": false,
  "rejection_reason": ""
}
```

[SPECIFICATION]
**Pre-Condition of this generation task**:
  * `transition_exists_in_delta` is true.
  * `[Pre-state Evidence: P / source]` is the only executable UI read scope for
    `precondition` / `Gamma`.
  * `[Post-state Evidence: Q / target]` is the executable UI read scope for
    `postcondition` / `Psi`, and is background evidence for `Gamma`.
  * `[Global Information / Static APK Priors]` is prior knowledge only.
  * Local paths are provenance only; use full `xml_excerpt`, `compact_tree_text`,
    `alt_text`, source widget registry facts, and attached images as evidence.
  * The current runtime evaluator executes the listed `PredicateBasis`.
  * The postcondition admission path compiles `postcondition.predicates` and
    records `effect_requirements` as audit-only metadata.

**Legal Outcomes for Precondition `Gamma`**:

**Case 1 (Executable semantic guard)**:
  If source/action evidence supports a semantic binding to frozen user intent:
    * Emit a typed `GuardContract`.
    * Put it in both top-level `precondition` and top-level `contract`.
    * Declare every referenced `$intent.*` variable in `required_slots`.
    * Emit at least one predicate whose `expected.kind == "intent"`.
    * Prefer `action(input_text) == $intent.<slot>`,
      `action(target_text) == $intent.<slot>`, or
      `action(target_resource_id) == $intent.<slot>` for user/task-side input or
      item-selection bindings when action evidence supports it.
    * Set `semantic_binding_required` according to the transition's guard obligation.
    * Set `semantic_binding_incomplete = false`.

**Case 2 (Partial executable guard)**:
  If only structural/safety evidence is executable:
    * Emit only executable source/action predicates.
    * For semantic-required transitions, set
      `semantic_binding_incomplete = true`.
    * Explain the missing semantic binding in `notes` or `rejection_reason`.

**Case 3 (No sound executable guard)**:
  If no sound source/action predicate can be produced:
    * Emit `predicates = []`.
    * Set `required` and `semantic_binding_required` conservatively.
    * For semantic-required transitions, set
      `semantic_binding_incomplete = true`.
    * Set `rejection_reason` to an evidence-based reason.

**Legal Outcomes for Postcondition `Psi`**:

**Case P1 (Executable intent-conditioned effect check)**:
  If target/diff evidence supports checking that the action produced the intended
  effect after arrival:
    * Emit a typed `postcondition` contract.
    * Declare every referenced `$intent.*` variable in `postcondition.required_slots`.
    * Emit at least one postcondition predicate whose expected target fact is
      determined by the known action, a declared `$intent.*` slot, or the
      source-to-target effect evidence.
    * Prefer predicates over reflected target facts such as
      `value(message_list) contains $intent.message_text`,
      `value(history_list) contains $intent.amount`,
      `read(toggle, is_checked) == $intent.desired_state`, or
      `count(cart_items) > 0` when supported by Q/diff evidence.
    * Include `in_state(<target_state_id>)` when target arrival is part of the
      edge-local postcondition evidence.
    * Use postcondition predicates for target facts expressible as `in_state`,
      `read`, `count`, and `value`; use `contains` or `not_contains` as
      operators on `read` or `value` predicates.
    * Do not rely on source-to-target effect observations as executable DSL.
      `effect_requirements` are audit-only metadata and may explain a diff fact
      that admission will not attach as a runtime predicate.
    * Set `intent_effect_required` according to the transition's effect obligation.
    * Set `intent_effect_incomplete = false`.

**Case P2 (Partial executable postcondition)**:
  If only generic target-state evidence is executable:
    * Emit executable target-side predicates that are grounded in Q evidence.
    * For effect-required transitions, set `intent_effect_incomplete = true`.
    * Explain the missing intent-conditioned effect in `notes` or
      `rejection_reason`.

**Case P3 (No sound executable postcondition)**:
  If no sound target-side predicate can be produced:
    * Emit `postcondition.predicates = []`.
    * Set `postcondition.required` and `intent_effect_required` conservatively.
    * For effect-required transitions, set `intent_effect_incomplete = true`.

**Always-Enforced Rules**:
  * No FSM states, actions, transitions, replay confidence, or runtime verdicts are
    created or changed.
  * `known_action` remains unchanged.
  * `Gamma` / `precondition` is represented as a conjunction of typed predicates.
  * `Psi` / `postcondition` is represented as a conjunction of typed target-side
    predicates whose expected post-action facts are selected for the fixed transition.
  * Target-state consistency facts from `I` may be used as reusable evidence and
    context when deriving edge-local `Psi`.
  * Source-to-target observations that cannot be expressed as target-side predicates
    remain audit-only metadata in `effect_requirements` with an evidence-based reason.
  * The top-level `contract` is an exact compatibility alias of `precondition`.
  * Executable predicates may read only source evidence, known-action properties,
    literals supported by source/action evidence, and declared `$intent.*` slots.
  * Precondition predicates may not reference target-only UI.
  * Postcondition predicates may reference target UI observed in Q.
  * No executable predicate references an alias absent from `[Source widget registry]`.
  * For postcondition predicates, use target aliases/resource ids observed in Q.
  * If a before/after observation is useful for audit, place it in
    `effect_requirements` as a generic audit note; it will not compile to executable DSL.
  * Element predicates are executable only when the referenced registry entry exposes
    a runtime-resolvable `resource_id`; prefer such aliases.
  * Every predicate must be independently executable. A single non-executable predicate
    rejects the whole guard, so emit only predicates supported by evidence.
  * No executable predicate uses `$bind.*`.
  * No executable predicate uses an undeclared `$intent.*`.
  * No executable predicate uses a predicate outside `PredicateBasis`.
  * For `postcondition.predicates`, prefer only `in_state`, `read`, `count`, and
    `value`, with `contains`/`not_contains` expressed as operators.
  * Do not assert literal equality against a source-known string property unless it
    matches the registry value.
  * Static APK priors never prove runtime presence, transition existence, or safety,
    and never prove post-state effects.
  * Enabledness/clickability alone never completes a semantic-required precondition.
  * Generic arrival facts may be included in `Psi` as edge-local arrival
    confirmation. Intent-effect-required postconditions still need a grounded
    target-side action/intent predicate or an explicit incompleteness flag.
  * The top-level `semantic_binding_incomplete` mirrors
    `precondition.semantic_binding_incomplete`; keep them consistent.
  * The top-level `postcondition_incomplete` mirrors
    `postcondition.intent_effect_incomplete`; keep them consistent.

**System Algorithm**:
  1. Classify action kind and guard obligation using the Action Impact Taxonomy from
     source/action evidence, target effect, siblings, and static priors as hints.
  2. Decide whether semantic binding is required.
  3. Decide whether a target-side postcondition is required.
  4. Select precondition predicates only from source/action evidence.
  5. Select postcondition predicates only from target evidence whose
     expected value is tied to the known action or frozen intent.
  6. Declare `$intent.*` slots separately in precondition and postcondition when used.
  7. Put UI/action-side binding needs into `binding_requirements`; do not compile them.
  8. Return the appropriate precondition case and postcondition case.

## Refine Prompt
[SPECIFICATION of Hoare Guard Scope]
**Pre-Condition**:
  * A `HOARE_TRANSITION_EVIDENCE_PACKET` is provided.

**Post-Condition**:
  * The generated precondition object is `Gamma`.
  * The generated postcondition object is `Psi`, the transition-effect contract for
    the fixed edge.
  * The generated postcondition is interpreted together with target-state
    invariants `I(Q)` during the post-action verifier phase.
  * Target evidence may affect Gamma only through classification, postcondition
    metadata, notes, binding requirements, or rejection reasons.
  * Target evidence is the executable read scope for target-side predicates in Psi.

[SPECIFICATION of Guard Predicate Conjunction]
**Pre-Condition**:
  * A `precondition`, `postcondition`, or compatibility `contract` predicate list
    is emitted.

**Post-Condition**:
  * `precondition.predicates` denotes `predicate_1 AND predicate_2 AND ... AND
    predicate_n` for Gamma.
  * `postcondition.predicates` denotes `predicate_1 AND predicate_2 AND ... AND
    predicate_n` for Psi.
  * `contract.predicates` is identical to `precondition.predicates`.
  * Empty predicate lists mean no executable predicate could be soundly emitted for
    that side; they do not authorize inventing a natural-language predicate.
  * Each predicate is typed by `PredicateBasis`.
  * Do not emit natural-language pseudo-predicates such as `visible(...)`,
    `textexists(...)`, `selected(...)`, `matches(...)`, or `is_recipient(...)`.
  * Source-to-target observations that are not target-side predicates, if emitted, are
    typed `postcondition.effect_requirements` for audit only and are not executable DSL.

[SPECIFICATION of Executability Admission]
**Pre-Condition**:
  * A predicate is proposed for executable admission in `precondition` or
    `postcondition`.

**Post-Condition**:
  * Precondition element predicates must use a source registry alias backed by
    `resource_id`, or a known source `resource_id` itself.
  * Precondition `action(type)` is normalized to `action(action_type)`.
  * Precondition action predicates may use only `action_type`, `target_text`,
    `target_resource_id`, `target_content_desc`, or `input_text`.
  * Precondition predicates may not read target-only UI.
  * Postcondition element predicates must use a target alias/resource id observed in Q.
  * Before/after effect observations are not executable admission predicates.
  * `in_state` requires `args.state`; `time_in` requires `args.start` and `args.end`.
  * If any predicate violates these rules, omit it from the relevant side. If no
    sound precondition predicate remains, return Case 3 for Gamma; if no sound
    postcondition predicate remains, return Case P3 for Psi.

[SPECIFICATION of `$intent.*`]
**Pre-Condition**:
  * A precondition or postcondition predicate uses `expected.kind == "intent"`.

**Post-Condition**:
  * `expected.slot` is non-null.
  * The same side's `required_slots` contains the slot name.
  * The slot denotes user/task intent, not UI-side row binding.
  * If the same intent slot appears in both Gamma and Psi, use the same slot name,
    type, and value-domain description on both sides.
  * Common executable forms include `action(input_text) == $intent.<slot>` and
    `action(target_text) == $intent.<slot>` when supported by the known action.
  * Common postcondition forms include
    `value(target_list) contains $intent.<slot>`,
    `value(target_field) == $intent.<slot>`, and
    `read(target_toggle, is_checked) == $intent.<slot>` when supported by Q/diff
    evidence.
  * When Q shows a value selected or written by the action, reuse the same slot name
    from Gamma so the verifier can connect pre-action intent binding to post-action
    effect evidence.

[SPECIFICATION of `$bind.*`]
**Pre-Condition**:
  * Correctness depends on UI/action-side binding that cannot be evaluated by the
    current DSL evaluator.

**Post-Condition**:
  * Add an item to `binding_requirements`.
  * Do not put `$bind.*` in precondition or postcondition predicates.
  * Set `semantic_binding_incomplete = true` if no executable `$intent.*` predicate
    captures the required semantic binding.
  * If the missing binding is an arrival/effect check, record it in
    `postcondition.effect_requirements` and set `postcondition.intent_effect_incomplete`
    plus top-level `postcondition_incomplete`.

[SPECIFICATION of Pre/Postcondition Obligation Policy]
**Pre-Condition**:
  * A transition must be assigned precondition `kind`, `required`, and
    `semantic_binding_required`.
  * A transition must be assigned postcondition `kind`, `required`, and
    `intent_effect_required`.

**Post-Condition**:
  * Use the evidence packet, not a closed keyword list, to classify the transition.
    Text labels and resource ids are evidence hints, not the policy.
  * Set precondition `required = false` and `semantic_binding_required = false` when the
    transition is only state_topology/navigation and no user-specific semantic
    binding is needed.
  * Set precondition `required = true` and `semantic_binding_required = true` when
    the transition selects or edits a user-intended item/value/content but the effect
    is local or reversible before the final commit.
  * Set precondition `required = true` and `semantic_binding_required = true` when
    Q/siblings show that the action commits previously chosen values to an externally
    visible, destructive, financial, privacy-sensitive, or authority-granting effect.
  * For semantic-required actions, generate predicates that bind the
    relevant user intent dimension whenever source/action evidence supports it:
    item identity, recipient/account/address identity, amount/value, content, target
    file/resource, permission/scope, or confirmation choice.
  * If only enabledness/clickability/source presence is executable, emit that partial
    guard only as Case 2 and set `semantic_binding_incomplete = true`.
  * Set postcondition `required = true` and `intent_effect_required = true` when Q
    should expose a target-side fact showing that the intended value/content/item/state
    is visible, selected, recorded, or committed.
  * Set postcondition `required = false` and `intent_effect_required = false` for
    pure topology transitions whose target consistency is fully represented by state
    invariants.
  * If only generic arrival evidence is executable for an effect-required transition,
    emit Case P2 and set `postcondition_incomplete = true`.
  * Give `postcondition.kind = "arrival_state"` to topology-only postcondition
    metadata. Give `postcondition.kind` one of `content_effect`, `item_added`,
    `item_removed`, `message_sent`, `payment_or_transfer`, `toggle_effect`, or
    `form_effect` when executable target-side predicates check a transition-specific
    arrival fact or committed value.
  * Record the taxonomy basis in precondition/postcondition `provenance` or `notes`, for example
    `impact:semantic_binding`, `impact:external_effect`,
    `reversibility:irreversible`, `binding:item_identity`, or
    `effect:intent_content_visible`.

[SPECIFICATION of Static APK Priors]
**Pre-Condition**:
  * Static APK prior fields are provided.

**Post-Condition**:
  * Use them only for role/domain/postcondition hints.
  * Do not admit precondition or postcondition predicates based solely on static prior.
  * Do not create transitions, prove postconditions, or produce runtime verdicts from
    static prior.

[SPECIFICATION of Invalid Output]
**Pre-Condition**:
  * A candidate predicate would require invented aliases, invented literals,
    target-only elements in Gamma, source-only elements in Psi, unsupported vocabulary,
    unsupported expected kinds, undeclared intent slots, or any non-executable
    admission rule.

**Post-Condition**:
  * Omit the invalid predicate from the relevant side.
  * If omitting it makes Gamma incomplete, set
    `precondition.semantic_binding_incomplete = true` and top-level
    `semantic_binding_incomplete = true`.
  * If omitting it makes Psi incomplete, set
    `postcondition.intent_effect_incomplete = true` and top-level
    `postcondition_incomplete = true`.
  * If no sound precondition predicate remains, return Case 3 for Gamma.
  * If no sound postcondition predicate remains, return Case P3 for Psi.
