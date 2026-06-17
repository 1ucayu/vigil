# VigilMarket — Gold Artifacts

These files are **evaluator-only ground truth** for the Vigil pipeline. They are *not* read by the running app.

| File | Purpose |
|------|---------|
| `fsm.json`   | Ground-truth FSM `M_A = <S, s0, Sigma, delta, Gamma, I, rho>` for VigilMarket. |
| `guards.json`| DSL guards `Gamma(s, a)` and invariants `I(s, a)` for guard-required transitions. |
| `tasks.json` | Benchmark tasks with intent slots and per-step expected verifier verdicts.    |

## How an evaluator should use these

1. Run the Vigil offline pipeline (Stage 0..5) against this app to produce a learned FSM and guard set.
2. Compare the learned model to `fsm.json` for state, action, and transition recall/precision.
3. Replay each task in `tasks.json` through the online verifier:
   - For each step, the verifier should emit one of `ALLOW`, `DENY`, `UNCERTAIN`.
   - Compare against `expected_verdict`.
   - Confirm the run ends at `expected_terminal` (or stops at `expected_denial_at` for negative tasks).
4. Use `guards.json` as the upper bound for guard quality. A learned guard is correct iff it accepts the same set of action contexts as the gold DSL on the test traces.

## Allowed transition kinds (mirrors `fsm.json`)

`nav`, `dialog_open`, `dialog_confirm`, `dialog_cancel`, `system_back`, `terminal`.

## Note on Settings toggles

`settings.high_contrast` and `settings.confirm_payments` are flagged `inert: true` in `fsm.json` and **must not** alter the checkout/payment topology. `settings.reset_demo` resets every piece of FSM-visible state *and* the inert toggles, so a fresh launch and an in-app reset produce identical fingerprints.
