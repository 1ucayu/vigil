[PROMPT]
Generate one typed `GuardContract` for one existing mobile-GUI FSM transition.

Use this file as a structured specification:
```text
[RELY]:
  Defines inputs, evidence packet, verifier interface, and read-only facts.

[GUARANTEE]:
  Defines the required output contract and field-level obligations.

[SPECIFICATION]:
  Defines preconditions, legal outcomes, always-enforced rules, and synthesis algorithm.

[SPECIFICATION of ...]:
  Defines normative refinement rules for specific subcontracts.
```

If [RELY] provides evidence that cannot be used under [SPECIFICATION], follow
[SPECIFICATION] and return a partial or rejected contract rather than using that
evidence unsafely.

The transition is presented as Hoare-style read-only evidence:
```text
{ Gamma(source screen P, known_action properties, frozen $intent.*) }
known_action
{ target_state / effect-only evidence Q }
```

`known_action` is fixed by the FSM. Synthesize only `Gamma`, the pre-action
transition guard. `$bind.*` is recorded separately in `binding_requirements`; it is
not an executable `Gamma` predicate.

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
  generated_object: Gamma
  generated_object_kind: typed GuardContract candidate
  executable_backend: conjunction of admitted DSL predicates

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
    xml_excerpt: string
    compact_tree_text: string
    screenshot_image?: image
    alt_text: string
    xml_tree_path: string       // provenance only
    screenshot_path: string     // provenance only
    page_function?: string
    display_name?: string

  [Source guard registry]:
    entries: map<alias, WidgetRegistryEntry>
    meaning: only legal element aliases for executable element predicates
    contents:
      - actionable widgets: click/input/toggle/select/navigation/confirm/cancel controls
      - readable semantic widgets: runtime-present labels, summary values, dialog text,
        selected option labels, recipient/account/address labels, amount/price/total labels,
        message/content previews, status/error text, permission/scope text
    requirement:
      - every entry must be observed in the source pre-state P
      - every executable element predicate must reference an entry in this registry
      - non-interactable readable TextView/label entries are legal when they expose
        runtime-readable properties such as text, value, resource_id, class_name, is_enabled

  [Post-state Evidence: Q / target]:
    role: effect-only semantic evidence
    state_id: string
    screen_id: string
    xml_excerpt: string
    compact_tree_text: string
    screenshot_image?: image
    alt_text: string
    xml_tree_path: string       // provenance only
    screenshot_path: string     // provenance only
    page_function?: string
    display_name?: string

  [Source-to-target semantic/evidence diff]:
    diff_summary: string
    role: effect-only semantic disambiguation

  [Sibling outgoing actions]:
    actions: list<Action>
    role: distinguish choices, forms, commits, navigation, cancel/confirm, repeated rows

  [Global Information / Static APK Priors]:
    manifest_activity_labels?: list<string>
    permissions?: list<string>
    resource_strings?: map<string, string>
    string_arrays?: map<string, list<string>>
    layout_widget_declarations?: list<string>
    role: semantic role/domain/risk hints only; not runtime proof

  [Verifier Basis]:
    predicate_vocabulary: PredicateBasis
    readable_element_properties: set<Property>
    readable_action_properties: set<Property>
    output_schema: GuardContract

  [Action Impact Taxonomy]:
    role: classify why a guard is needed without hardcoding app-specific labels
    dimensions:
      state_topology:
        meaning: screen navigation, modal open/close, back/cancel, tab change
        usual_risk: low unless it commits state or hides a destructive step
      semantic_binding:
        meaning: choosing or confirming intended item/person/account/address/file/row/value
        usual_risk: medium when wrong binding changes task meaning
      local_reversible_state:
        meaning: editable local app state that can be corrected before final commit
        usual_risk: medium when it affects later commit; low when purely cosmetic
      irreversible_or_costly_state:
        meaning: changes that are hard to undo, destructive, paid, externally visible,
                 security-sensitive, privacy-sensitive, or permission/authority granting
        usual_risk: high
      external_side_effect:
        meaning: communication, publication, order placement, transfer, payment,
                 account/security change, data deletion, permission grant, or device/app
                 state change outside the current screen
        usual_risk: high unless evidence proves the effect is reversible and local
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
         semantic_label | amount_label | address_label | recipient_label |
         account_label | item_label | message_preview | status_text | unknown
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
  in_state(state_name)        // args.state in GuardContract JSON
  time_in(start, end)         // args.start and args.end in GuardContract JSON

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
  "contract": {
    "kind": "none|navigation|item_binding|input_binding|toggle_binding|form_check|confirm_commit|safety_check|unknown",
    "required": true,
    "risk_level": "low|medium|high|unknown",
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
```

[SPECIFICATION]
**Pre-Condition**:
  * `transition_exists_in_delta` is true.
  * `[Pre-state Evidence: P / source]` is the only executable UI read scope.
  * `[Post-state Evidence: Q / target]` is effect-only evidence.
  * `[Global Information / Static APK Priors]` is prior knowledge only.
  * Local paths are provenance only; use full `xml_excerpt`, `compact_tree_text`,
    `alt_text`, source guard registry facts, and attached images as evidence.
  * The current runtime evaluator executes only the listed `PredicateBasis`.

**Legal Outcomes**:

**Case 1 (Executable semantic guard)**:
  If source/action evidence supports a semantic binding to frozen user intent:
    * Emit a typed `GuardContract`.
    * Declare every referenced `$intent.*` variable in `required_slots`.
    * Emit at least one predicate whose `expected.kind == "intent"`.
    * Prefer `read(source_semantic_widget, text|value) == $intent.<slot>` for
      source summary/confirmation facts such as product, recipient, account, address,
      amount, message/content preview, permission scope, or selected option.
    * Prefer `action(input_text) == $intent.<slot>`,
      `action(target_text) == $intent.<slot>`, or
      `action(target_resource_id) == $intent.<slot>` only when action evidence itself
      carries the user/task-side value.
    * Set `semantic_binding_required` according to action risk.
    * Set `semantic_binding_incomplete = false`.

**Case 2 (Partial executable guard)**:
  If only structural/safety evidence is executable:
    * Emit only executable source/action predicates.
    * For high-risk or semantic-required transitions, set
      `semantic_binding_incomplete = true`.
    * Explain the missing semantic binding in `notes` or `rejection_reason`.

**Case 3 (No sound executable guard)**:
  If no sound source/action predicate can be produced:
    * Emit `predicates = []`.
    * Set `risk_level` and `required` conservatively.
    * For high-risk or semantic-required transitions, set
      `semantic_binding_incomplete = true`.
    * Set `rejection_reason` to an evidence-based reason.

**Always-Enforced Rules**:
  * No FSM states, actions, transitions, replay confidence, or runtime verdicts are
    created or changed.
  * `known_action` remains unchanged.
  * `Gamma` is represented as a conjunction of typed predicates.
  * Executable predicates may read only source evidence, known-action properties,
    literals supported by source/action evidence, and declared `$intent.*` slots.
  * No executable predicate references target-only UI.
  * No executable predicate references an alias absent from `[Source guard registry]`.
  * Element predicates are executable only when the referenced registry entry exposes
    a runtime-resolvable `resource_id`; prefer such aliases.
  * Runtime-present non-interactable readable widgets are legal predicate targets.
  * Every predicate must be independently executable. A single non-executable predicate
    rejects the whole guard, so emit only predicates supported by evidence.
  * No executable predicate uses `$bind.*`.
  * No executable predicate uses an undeclared `$intent.*`.
  * No executable predicate uses a predicate outside `PredicateBasis`.
  * Do not assert literal equality against a source-known string property unless it
    matches the registry value.
  * Static APK priors never prove runtime presence, current runtime values, transition
    existence, safety, or post-state checks.
  * Enabledness/clickability alone never completes a high-risk or semantic-required guard.
  * The top-level `semantic_binding_incomplete` mirrors
    `contract.semantic_binding_incomplete`; keep them consistent.

**System Algorithm**:
  1. Classify action kind and risk using the Action Impact Taxonomy from
     source/action evidence, target effect, siblings, and static priors as hints.
  2. Identify the user-intent dimensions that must be verified before `known_action`.
  3. Match each intent dimension to a runtime-present source guard registry entry.
  4. Select only source/action predicates from `PredicateBasis`.
  5. Declare `$intent.*` slots for user/task-side values.
  6. Put non-executable UI/action-side binding needs into `binding_requirements`.
  7. Return Case 1, Case 2, or Case 3.

## Refine Prompt
[SPECIFICATION of Hoare Guard Scope]
**Pre-Condition**:
  * A `HOARE_TRANSITION_EVIDENCE_PACKET` is provided.

**Post-Condition**:
  * The generated object is only `Gamma`.
  * Target evidence may affect only classification, risk, notes, binding requirements,
    or rejection reasons.

[SPECIFICATION of Guard Predicate Conjunction]
**Pre-Condition**:
  * `contract.predicates` is non-empty.

**Post-Condition**:
  * The contract denotes `predicate_1 AND predicate_2 AND ... AND predicate_n`.
  * Each predicate is typed by `PredicateBasis`.
  * Do not emit natural-language pseudo-predicates such as `visible(...)`,
    `textexists(...)`, `selected(...)`, `matches(...)`, or `is_recipient(...)`.

[SPECIFICATION of Executability Admission]
**Pre-Condition**:
  * A predicate is proposed for executable admission.

**Post-Condition**:
  * Element predicates must use a source guard registry alias backed by `resource_id`,
    or a known source `resource_id` itself.
  * `action(type)` is normalized to `action(action_type)`.
  * Action predicates may use only `action_type`, `target_text`,
    `target_resource_id`, `target_content_desc`, or `input_text`.
  * `in_state` requires `args.state`; `time_in` requires `args.start` and `args.end`.
  * If any predicate violates these rules, omit it. If no sound predicate remains,
    return Case 3.

[SPECIFICATION of Source Semantic Facts]
**Pre-Condition**:
  * A runtime-present source registry entry exposes a readable semantic fact such as
    product, amount, recipient, account, address, message preview, selected option,
    permission scope, dialog text, status, or error text.

**Post-Condition**:
  * For medium/high semantic-required transitions, prefer executable predicates that
    bind these source facts to declared `$intent.*` slots.
  * Use exact `read(element, text) == $intent.<slot>` or `value(element) == $intent.<slot>`
    forms when the entry exposes `text` or `value`.
  * Do not compare to the observed literal value when the user's requested value is the
    semantic source of truth; use `$intent.*`.

[SPECIFICATION of `$intent.*`]
**Pre-Condition**:
  * A predicate uses `expected.kind == "intent"`.

**Post-Condition**:
  * `expected.slot` is non-null.
  * `required_slots` contains the same slot name.
  * The slot denotes user/task intent, not UI-side row binding.

[SPECIFICATION of `$bind.*`]
**Pre-Condition**:
  * Correctness depends on UI/action-side binding that cannot be evaluated by the
    current DSL evaluator.

**Post-Condition**:
  * Add an item to `binding_requirements`.
  * Do not put `$bind.*` in predicates.
  * Set `semantic_binding_incomplete = true` if no executable `$intent.*` predicate
    captures the required semantic binding.

[SPECIFICATION of Taxonomy-Driven Risk Policy]
**Pre-Condition**:
  * A transition must be assigned `kind`, `risk_level`, `required`, and
    `semantic_binding_required`.

**Post-Condition**:
  * Use the Action Impact Taxonomy, not a closed keyword list, to classify the
    transition. Text labels and resource ids are evidence hints, not the policy.
  * Set `risk_level = "low"` and `required = false` when the transition is only
    state_topology/navigation and no user-specific semantic binding is needed.
  * Set `risk_level = "medium"`, `required = true`, and
    `semantic_binding_required = true` when the transition selects or edits a
    user-intended item/value/content but the effect is local or reversible before
    the final commit.
  * Set `risk_level = "high"`, `required = true`, and
    `semantic_binding_required = true` when the transition has an
    irreversible_or_costly_state or external_side_effect impact, or when Q/siblings
    show that the action commits previously chosen values to a safety-sensitive,
    privacy-sensitive, externally visible, destructive, financial, or
    authority-granting effect.
  * For medium/high semantic-required actions, generate predicates that bind the
    relevant user intent dimension whenever source/action evidence supports it:
    item identity, recipient/account/address identity, amount/value, content, target
    file/resource, permission/scope, or confirmation choice.
  * If only enabledness/clickability/source presence is executable, emit that partial
    guard only as Case 2 and set `semantic_binding_incomplete = true`.
  * Record the taxonomy basis in `provenance` or `notes`, for example
    `impact:semantic_binding`, `impact:external_side_effect`,
    `reversibility:irreversible`, or `binding:item_identity`.

[SPECIFICATION of Static APK Priors]
**Pre-Condition**:
  * Static APK prior fields are provided.

**Post-Condition**:
  * Use them only to enrich semantic roles, value domains, closed option sets, or risk
    hints for widgets that are also runtime-present in source P.
  * Static priors may identify that a runtime-present widget is an amount field,
    recipient field, address field, product field, destructive action, payment action,
    permission scope, or closed-set option.
  * Static priors must not create source registry entries absent from source P.
  * Static priors must not provide current runtime values for predicates.
  * Static priors must not prove transition existence, action availability, safety, or
    runtime verdicts.

[SPECIFICATION of Invalid Output]
**Pre-Condition**:
  * A candidate predicate would require invented aliases, invented literals, target-only
    elements, unsupported vocabulary, unsupported expected kinds, undeclared intent
    slots, or any non-executable admission rule.

**Post-Condition**:
  * Omit the invalid predicate.
  * If omitting it makes the guard incomplete, set `semantic_binding_incomplete = true`.
  * If no sound predicate remains, return Case 3 from [SPECIFICATION].
