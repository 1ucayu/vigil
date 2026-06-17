# VigilChat - Gold Artifacts

These files are **evaluator-only ground truth** for the Vigil pipeline. They are *not* read by the running app.

| File | Purpose |
|------|---------|
| `fsm.json`   | Ground-truth FSM `M_A = <S, s0, Sigma, delta, Gamma, I, rho>` for VigilChat. |
| `guards.json`| DSL guards `Gamma(s, a)` and invariants `I(s, a)` for guard-required transitions. |
| `tasks.json` | Benchmark tasks with intent slots and per-step expected verifier verdicts.   |

## How an evaluator should use these

1. Run the Vigil offline pipeline against this app to produce a learned FSM and guard set.
2. Compare the learned model to `fsm.json` for state, action, and transition recall/precision.
3. Replay each task in `tasks.json` through the online verifier:
   - For each step, the verifier should emit one of `ALLOW`, `DENY`, `UNCERTAIN`.
   - Compare against `expected_verdict`.
   - Confirm the run ends at `expected_terminal` (or stops at `expected_denial_at`).
4. Use `guards.json` as the upper bound for guard quality.

## Allowed transition kinds (mirrors `fsm.json`)

`nav`, `dialog_open`, `dialog_confirm`, `dialog_cancel`, `system_back`, `terminal`.

## Notes

- `thread` is a templated state with parameter `thread_id`; instantiated values are `alice`, `bob`, `dad` (plus any contact id opened from `contacts`).
- `message_options` and `delete_confirm` are dialog states anchored on `thread`. They own the screen marker while active (the base thread screen suppresses its marker), so the UI tree always contains exactly one `screen_marker` node.
- Message ids are monotonic per thread: appending uses `m_<thread_id>_<seq>` with `seq` advancing on every send/attachment; deletion does not decrement the counter, so deleted ids never re-appear in the lifetime of the demo. `settings.reset_demo` restores both the messages map and the per-thread sequence map to the seeded values.
