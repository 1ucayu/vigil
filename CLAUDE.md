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
| **Target venue** | MobiCom 2027 (deadline ~2026.08) |

---

## 2. One-Paragraph Summary

Vigil is a **neuro-symbolic runtime verification system** for mobile GUI agents. In the **offline (neuro) phase**, an LLM systematically explores a target Android app via Accessibility Service, abstracts raw screens into states, and constructs a **per-app hierarchical Finite State Machine (FSM)** annotated with **DSL semantic guards**. The FSM is then verified via test-case generation and on-device replay. In the **online (symbolic) phase**, a lightweight engine performs **dual-layer formal verification** — FSM structural checks (transition validity, reachability, invariants) and DSL semantic checks (guard condition evaluation) — with an optional **LLM fallback** invoked only when the symbolic layer cannot produce a definitive ALLOW/DENY. Crucially, Vigil is **self-evolving**: when encountering previously unseen UI states (e.g., dynamic content in food-delivery or e-commerce apps), it degrades gracefully through a **three-tier verification** strategy (structural FSM → parameterized guards → online micro-evolution) and caches evolution results back into the FSM bundle, achieving **monotonically increasing coverage over time**.

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

---

## 4. System Architecture

### 4.1 Offline Pipeline (Neuro Layer — 5 Stages)

**Stage 1: UI Exploration** (`vigil.neuro.explorer`)
- Connect to Android device via `uiautomator2`
- BFS/DFS traversal: at each screen, enumerate interactable elements, execute each action, record resulting screen
- For each screen: save accessibility tree XML + screenshot PNG + element list
- Accessibility Service provides: `className`, `resourceId`, `text`, `contentDescription`, `bounds`, `isClickable`, `isScrollable`, `isEditable`, `isChecked`, `isEnabled`
- No root or developer mode needed
- Action templates:
  ```python
  ACTION_TEMPLATES = {
      'clickable': ['click'],
      'long_clickable': ['long_press'],
      'editable': ['input_text'],
      'scrollable': ['scroll_up', 'scroll_down'],
      'checkable': ['click'],
  }
  # Plus global: 'navigate_back', 'navigate_home'
  ```
- Output: saved to `data/apps/<app_name>/` as JSON with screens, elements, transitions

**Stage 2: State Abstraction** (`vigil.neuro.state_abstractor`)
- Raw screens too fine-grained (dynamic content differs each time) → need abstraction
- Phase 1 (rule-based): structural fingerprint = hash of (class_name, resource_id, depth, interactability) — ignores text/content
- Phase 2 (LLM-assisted): for ambiguous cases, ask LLM "are these two screens the same UI state?" + name states ("PaymentConfirm", "WiFiListPage")
- Output: `S = {s₁, s₂, ..., sₙ}` and `T ⊆ S × Action × S`

**Stage 3: Hierarchical FSM Construction** (`vigil.neuro.fsm_builder`)
- Hierarchy: App > Activity > Fragment > Component (inspired by "Learned Cloud Emulators", HotNets'25)
- Constrains transition scope (fragment button can't directly modify another activity's state) → mitigates state explosion
- Uses Android Activity name from accessibility tree to group states
- Built on `networkx.DiGraph`

**Stage 4: DSL Guard Generation** (`vigil.neuro.dsl_generator`)
- Annotate each FSM transition with semantic guard conditions
- Uses constrained formal grammar + constrained decoding to ensure syntactic correctness
- Guard grammar (Lark):
  ```
  guard ::= predicate | predicate && guard | predicate || guard | !predicate | (guard)
  predicate ::= read(element, property) op value | time_in(HH:MM, HH:MM) | in_state(name) | value(element) op value
  ```
- Examples: payment → `read(amount_field, value) > 0 && read(amount_field, value) <= 5000`; messaging → `read(recipient_field, text) != ""`

**Stage 5: FSM Verification via Replay** (`vigil.neuro.replay_verifier`)
- Symbolic execution enumerates bounded-length paths → converts to test cases → replays on real device
- Each transition gets confidence score = success_count / total_trials
- Low-confidence transitions return UNCERTAIN at runtime instead of ALLOW/DENY

### 4.2 Online Engine (Symbolic Layer — Three-Tier Verification)

This is the key architectural evolution responding to advisor feedback about dynamic apps.

**Tier 1: Structural FSM Verification (pure symbolic)**
- State localization: accessibility tree fingerprinting → FSM state
- Transition validity: is action legal from current state?
- Reachability: can we still reach goal state? O(V+E)
- Invariant check: any state invariants violated?
- Confidence check: is this transition well-tested?
- Coverage: Settings ~99%, dynamic apps ~60%

**Tier 2: Parameterized Guard Verification (pure symbolic)**
- DSL guard templates cached offline, parameters bound at runtime
- Task State Machine tracks multi-step progress (solves sequential dependency)
- Example: milk-tea ordering uses intent checklist to verify each step fulfills user goal
- Predicate evaluation is O(R), R = number of rules

**Tier 3: Online LLM Micro-Evolution (infrequent)**
- Triggered only for truly unseen content patterns
- Most "unseen" states are structurally similar to known states → `inherit_and_bind` (no LLM needed)
- When LLM is needed: generate new state + guards → cache back into FSM bundle
- Creates learning loop: system coverage monotonically increases with use
- **This is a unique contribution** — no existing work lets a formal verification model self-evolve

**Decision Logic:**
```
VERIFY(current_screen, proposed_action, user_goal):

  state ← LOCALIZE(current_screen, FSM)
  IF state = UNKNOWN:
    // Tier 3: attempt structural similarity matching
    similar ← FIND_SIMILAR(current_screen, FSM)
    IF similar exists → inherit_and_bind(similar) → state
    ELSE → trigger LLM micro-evolution → UNCERTAIN (async cache result)

  // Tier 1: Structural FSM Check
  IF proposed_action ∉ FSM.transitions[state] → DENY
  target ← FSM.target(state, proposed_action)
  IF ∃ invariant I : I(target) = false → DENY
  IF goal ≠ null ∧ ¬REACHABLE(target, goal) → DENY
  IF FSM.confidence(state, proposed_action) < θ → UNCERTAIN

  // Tier 2: DSL Semantic Check
  guard ← FSM.guard(state, proposed_action)
  IF guard ≠ null ∧ EVAL(guard, current_screen) = false → DENY

  → ALLOW
```

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
│       │   ├── explorer.py         # Stage 1: UI Exploration (BFS/DFS via uiautomator2)
│       │   ├── state_abstractor.py # Stage 2: State Abstraction (fingerprint + LLM)
│       │   ├── fsm_builder.py      # Stage 3: Hierarchical FSM Construction
│       │   ├── dsl_generator.py    # Stage 4: DSL Semantic Guard Generation
│       │   ├── replay_verifier.py  # Stage 5: FSM Verification via Replay
│       │   └── evolution.py        # Tier 3: Online Micro-Evolution engine
│       │
│       ├── symbolic/               # ONLINE: Runtime verification engine
│       │   ├── __init__.py
│       │   ├── state_locator.py    # Screen → FSM state mapping (fingerprinting + similarity)
│       │   ├── fsm_checker.py      # Tier 1: FSM structural verification
│       │   ├── dsl_evaluator.py    # Tier 2: DSL semantic verification (guard eval)
│       │   ├── decision_engine.py  # Combined ALLOW / DENY / UNCERTAIN + tier routing
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
│       │   ├── llm_client.py      # Unified LLM client wrapper (Anthropic / OpenAI)
│       │   └── config.py          # Pydantic config models + YAML loader
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
│   ├── dsl_grammar.lark           # Formal grammar for DSL guards
│   └── architecture.md            # Architecture diagrams & decisions
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
import networkx as nx
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

class HierarchyLevel(Enum):
    APP = "app"
    ACTIVITY = "activity"
    FRAGMENT = "fragment"
    COMPONENT = "component"

@dataclass
class AbstractState:
    state_id: str
    name: str                          # human-readable: "PaymentConfirm"
    fingerprint: str
    hierarchy_level: HierarchyLevel
    parent_state: Optional[str]        # parent in hierarchy
    activity_name: Optional[str]       # Android Activity class
    invariants: list[str] = field(default_factory=list)
    raw_screens: list[str] = field(default_factory=list)

@dataclass
class Transition:
    source: str
    target: str
    action: dict                       # {"type": "click", "target": ...}
    guard: Optional[str] = None        # DSL guard expression
    confidence: float = 0.0            # replay confidence score
    observed_count: int = 0

class AppFSM:
    def __init__(self, app_package: str):
        self.app_package = app_package
        self.graph = nx.DiGraph()
        self.states: dict[str, AbstractState] = {}
        self.transitions: list[Transition] = []
        self.initial_state: Optional[str] = None
        self.version: str = "0.1.0"
        self.evolution_log: list[dict] = []

    def add_state(self, state: AbstractState): ...
    def add_transition(self, trans: Transition): ...
    def is_valid_transition(self, from_state: str, action: dict) -> bool: ...
    def is_reachable(self, from_state: str, goal_state: str) -> bool: ...
    def get_shortest_path(self, from_state: str, goal_state: str) -> list: ...
    def get_transition_target(self, from_state: str, action: dict) -> Optional[str]: ...
    def get_transition(self, from_state: str, action: dict) -> Optional[Transition]: ...
    def find_similar_state(self, fingerprint: str, threshold: float) -> Optional[str]: ...
    def serialize(self, path: str): ...

    @classmethod
    def deserialize(cls, path: str) -> 'AppFSM': ...
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

read_pred: "read(" ELEMENT "," PROPERTY ")" OP VALUE
time_pred: "time_in(" TIME "," TIME ")"
state_pred: "in_state(" STATE_NAME ")"
value_pred: "value(" ELEMENT ")" OP VALUE

OP: "==" | "!=" | ">" | "<" | ">=" | "<="
ELEMENT: /[a-zA-Z_][a-zA-Z0-9_.]*/
PROPERTY: /[a-zA-Z_][a-zA-Z0-9_]*/
STATE_NAME: /[a-zA-Z_][a-zA-Z0-9_]*/
VALUE: ESCAPED_STRING | NUMBER | "true" | "false" | "null"
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

llm:
  provider: "anthropic"              # anthropic | openai
  model: "claude-sonnet-4-20250514"
  max_tokens: 4096
  temperature: 0.0

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

1. **Start with Settings app** — deterministic, no login, no network dependency, ideal for debugging
2. **`src/` layout is mandatory** — all code under `src/vigil/`, never import from repo root
3. **Core novelty = FSM pipeline + self-evolution** — don't over-engineer Android infrastructure early
4. **Keep symbolic verifier in pure Python first** — graph lookups + predicate eval are fast enough; only port to Kotlin/C++ if profiling justifies it
5. **Verifier is agent-agnostic** — wraps ANY GUI agent as safety layer, does not replace agent
6. **Replay non-determinism is expected** — use confidence scores, don't chase 100% reliability
7. **State explosion mitigation**: hierarchy + bounded exploration (max 500 steps per app initially)
8. **Never commit `data/` or `models/bundles/`** — large generated artifacts
9. **All LLM calls are offline only** — runtime symbolic layer must NEVER call an LLM (except Tier 3, which is async and infrequent)
10. **uv is the only package manager** — no pip, no requirements.txt
11. **Reference code to borrow**:
    - V-Droid (`html_representation.py`): UI parsing, element filtering, display_id assignment → adapt for `core/ui_parser.py`
    - V-Droid (action enumeration): element properties → candidate actions → adapt for `core/action_types.py`
    - VeriSafe (predicate patterns): per-app guard templates (payment, messaging, shopping) → inspiration for `neuro/dsl_generator.py`
    - VeriSafe (ADB + screenshot + tree capture): simpler UI capture pipeline → reference for `neuro/explorer.py`

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
