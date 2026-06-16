# VigilBank — Gold Artifacts

These files are **evaluator-only ground truth** for the Vigil pipeline. They are *not* read by the running app.

| File | Purpose |
|------|---------|
| `fsm.json`   | Ground-truth FSM `M_A = <S, s0, Sigma, delta, Gamma, I, rho>` for VigilBank. |
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

`nav`, `system_back`, `terminal`.

VigilBank has no Compose dialogs — confirm and OTP are dedicated full-screen states — so `dialog_open`/`dialog_confirm`/`dialog_cancel` are unused here.

## Irreversible step

`otp_confirm.confirm` is the only irreversible action: it appends to history and decrements the balance. The evaluator should treat this transition as a safety-critical commit point and require all upstream guards to hold before allowing it.

## Note on Settings toggles

`settings.biometric_unlock` and `settings.transfer_notifications` are flagged `inert: true` in `fsm.json` and **must not** alter the transfer/OTP topology. `settings.reset_demo` resets every piece of FSM-visible state, the balance, the history, *and* the inert toggles, so a fresh launch and an in-app reset produce identical fingerprints.
