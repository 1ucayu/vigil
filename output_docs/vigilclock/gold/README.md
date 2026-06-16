# VigilClock — Gold Artifacts

These files are **evaluator-only ground truth** for the Vigil pipeline. They are *not* read by the running app.

| File | Purpose |
|------|---------|
| `fsm.json`   | Ground-truth FSM `M_A = <S, s0, Sigma, delta, Gamma, I, rho>` for VigilClock. |
| `guards.json`| DSL guards `Gamma(s, a)` and invariants `I(s, a)` for guard-required transitions. |
| `tasks.json` | Benchmark tasks with intent slots and per-step expected verifier verdicts.    |

## How an evaluator should use these

1. Run the Vigil offline pipeline (Stage 0..5) against this app to produce a learned FSM and guard set.
2. Compare the learned model to `fsm.json` for state, action, and transition recall/precision.
3. Replay each task in `tasks.json` through the online verifier:
   - For each step, the verifier should emit one of `ALLOW`, `DENY`, `UNCERTAIN`.
   - Compare against `expected_verdict`.
   - Confirm the run ends at `expected_terminal` (or stops at `expected_denial_at` for negative tasks).
4. Use `guards.json` as the upper bound for guard quality.

## Allowed transition kinds (mirrors `fsm.json`)

`nav`, `tick_done`, `system_back`, `terminal`.

## Note on volatile readouts

`timer.remaining` (on `timer_running` / `timer_paused` / `timer_done`) and
`stopwatch.elapsed` (on `stopwatch_running` / `stopwatch_paused`) carry text
that changes every tick. The `screen_marker` text in each of those states is
static and lives in its own composable, so the screen fingerprint remains
stable even while the readout updates.
