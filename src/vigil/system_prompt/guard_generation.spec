[PROMPT]
Generate one typed `GuardContract` for one existing mobile-GUI FSM transition.

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
    role: effect-only semantic evidence
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
    role: role/risk/domain hints only; not runtime proof

  [Verifier Basis]:
    predicate_vocabulary: PredicateBasis
    readable_element_properties: set<Property>
    readable_action_properties: set<Property>
    output_schema: GuardContract
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
  risk_hints: set<string>
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
    `alt_text`, source widget registry facts, and attached images as evidence.
  * The current runtime evaluator executes only the listed `PredicateBasis`.

**Legal Outcomes**:

**Case 1 (Executable semantic guard)**:
  If source/action evidence supports a semantic binding to frozen user intent:
    * Emit a typed `GuardContract`.
    * Declare every referenced `$intent.*` variable in `required_slots`.
    * Emit at least one predicate whose `expected.kind == "intent"`.
    * Prefer `action(input_text) == $intent.<slot>`,
      `action(target_text) == $intent.<slot>`, or
      `action(target_resource_id) == $intent.<slot>` for user/task-side input or
      item-selection bindings when action evidence supports it.
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
  * No executable predicate references an alias absent from `[Source widget registry]`.
  * Element predicates are executable only when the referenced registry entry exposes
    a runtime-resolvable `resource_id`; prefer such aliases.
  * Every predicate must be independently executable. A single non-executable predicate
    rejects the whole guard, so emit only predicates supported by evidence.
  * No executable predicate uses `$bind.*`.
  * No executable predicate uses an undeclared `$intent.*`.
  * No executable predicate uses a predicate outside `PredicateBasis`.
  * Do not assert literal equality against a source-known string property unless it
    matches the registry value.
  * Static APK priors never prove runtime presence, transition existence, or safety,
    and are never post-state checks.
  * Enabledness/clickability alone never completes a high-risk or semantic-required guard.
  * The top-level `semantic_binding_incomplete` mirrors
    `contract.semantic_binding_incomplete`; keep them consistent.

**System Algorithm**:
  1. Classify action kind and risk from source/action evidence, target effect, siblings,
     and static priors as hints.
  2. Decide whether semantic binding is required.
  3. Select only source/action predicates from `PredicateBasis`.
  4. Declare `$intent.*` slots for user/task-side values.
  5. Put UI/action-side binding needs into `binding_requirements`; do not compile them.
  6. Return Case 1, Case 2, or Case 3.

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
  * Element predicates must use a source registry alias backed by `resource_id`, or a
    known source `resource_id` itself.
  * `action(type)` is normalized to `action(action_type)`.
  * Action predicates may use only `action_type`, `target_text`,
    `target_resource_id`, `target_content_desc`, or `input_text`.
  * `in_state` requires `args.state`; `time_in` requires `args.start` and `args.end`.
  * If any predicate violates these rules, omit it. If no sound predicate remains,
    return Case 3.

[SPECIFICATION of `$intent.*`]
**Pre-Condition**:
  * A predicate uses `expected.kind == "intent"`.

**Post-Condition**:
  * `expected.slot` is non-null.
  * `required_slots` contains the same slot name.
  * The slot denotes user/task intent, not UI-side row binding.
  * Common executable forms include `action(input_text) == $intent.<slot>` and
    `action(target_text) == $intent.<slot>` when supported by the known action.

[SPECIFICATION of `$bind.*`]
**Pre-Condition**:
  * Correctness depends on UI/action-side binding that cannot be evaluated by the
    current DSL evaluator.

**Post-Condition**:
  * Add an item to `binding_requirements`.
  * Do not put `$bind.*` in predicates.
  * Set `semantic_binding_incomplete = true` if no executable `$intent.*` predicate
    captures the required semantic binding.

[SPECIFICATION of Risk Policy]
**Pre-Condition**:
  * Source/action/target-effect/sibling/static-prior evidence indicates a commit,
    safety-sensitive, or irreversible side effect.

**Post-Condition**:
  * Set `risk_level = "high"` and `required = true` for irreversible, destructive,
    financial, privacy/security, or permission-granting actions such as send, pay,
    transfer, checkout, buy, purchase, place order, delete, remove, allow, grant,
    or irreversible confirm.
  * Set `risk_level = "medium"`, `required = true`, and
    `semantic_binding_required = true` for reversible semantic commits such as submit,
    add-to-cart, attach/attachment selection, save/set/edit alarm, start timer, or
    stopwatch lap unless evidence clearly raises them to high risk.
  * For high-risk or semantic-required actions, prefer an executable semantic binding
    predicate. If only enabledness/clickability is executable, mark the contract
    semantically incomplete.

[SPECIFICATION of Static APK Priors]
**Pre-Condition**:
  * Static APK prior fields are provided.

**Post-Condition**:
  * Use them only for role/risk/domain hints.
  * Do not admit predicates based solely on static prior.
  * Do not create transitions, post-state checks, or runtime verdicts from static prior.

[SPECIFICATION of Invalid Output]
**Pre-Condition**:
  * A candidate predicate would require invented aliases, invented literals, target-only
    elements, unsupported vocabulary, unsupported expected kinds, undeclared intent
    slots, or any non-executable admission rule.

**Post-Condition**:
  * Omit the invalid predicate.
  * If omitting it makes the guard incomplete, set `semantic_binding_incomplete = true`.
  * If no sound predicate remains, return Case 3 from [SPECIFICATION].
