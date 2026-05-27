# VigilBank — Fidelity App

A small native Android app used by the [Vigil](../../README.md) project as a
controlled benchmark target, parallel to `vigilmarket`. Vigil's offline
pipeline is exercised against this app to test whether it can faithfully
construct and validate an FSM `M_A = <S, s0, Sigma, delta, Gamma, I, rho>`
for a banking/transfer flow with an irreversible OTP-gated commit.

The app is intentionally native and deterministic. It uses a Material3 shell:
top app bar, bottom navigation, cards, and summary panels. It does **not**
talk to the network, has no real authentication, holds no real money, and
ships only seeded in-memory data.

## Tooling

- Gradle: **8.7** (wrapper bytes copied verbatim from `vigilmarket`).
- Android Gradle Plugin: **8.6.1**.
- Kotlin: **1.9.24**, Compose Compiler: **1.5.14**, Compose BOM: **2024.06.00**.
- compileSdk / targetSdk: **34**, minSdk: **26**.
- JDK: **17+**.

If `local.properties` is missing, point it at your SDK:

```bash
echo "sdk.dir=$HOME/Library/Android/sdk" > local.properties
```

## Build / install / launch

```bash
cd fidelity_app/vigilbank
./gradlew assembleDebug
./gradlew installDebug
adb -s emulator-5554 shell monkey -p com.vigil.bank -c android.intent.category.LAUNCHER 1
```

## Implemented states

| State              | Screen marker             | Notes                                |
|--------------------|---------------------------|--------------------------------------|
| `home`             | `screen:home`             | Entry; balance card and quick actions. |
| `recipients`       | `screen:recipients`       | 3 seeded recipients.                 |
| `transfer_form`    | `screen:transfer_form`    | Amount + memo input; recipient bound. |
| `transfer_confirm` | `screen:transfer_confirm` | Inline error label on failed guard.  |
| `otp_confirm`      | `screen:otp_confirm`      | OTP entry; confirm = irreversible commit. |
| `transfer_success` | `screen:transfer_success` | Terminal.                            |
| `history`          | `screen:history`          | Seeded + newly committed transfers.  |
| `settings`         | `screen:settings`         | Inert toggles + `reset_demo`.        |

Unlike vigilmarket, VigilBank has **no Compose dialogs**: the confirm step
and OTP step are dedicated full-screen states. The `ScreenMarker` single-
authority rule is therefore satisfied trivially.

The Material3 bottom navigation bar is visible on every state. Its global
actions are tagged `nav.open_home`, `nav.open_recipients`, `nav.open_history`,
and `nav.open_settings`; these are documented in `gold/fsm.json` under
`global_navigation`.

System back is implemented via Compose `BackHandler` and is deterministic;
the back targets are first-class transitions of kind `system_back` in
`gold/fsm.json`. From `home`, system back exits the app.

## Canonical actions (selected)

```
click(home.open_recipients)
click(recipients.recipient_row.alice.open)
input(transfer.amount.input, "10000")
input(transfer.memo.input, "Rent")
click(transfer.continue)
click(transfer_confirm.submit)
click(otp_confirm.confirm)
click(transfer_success.back_home)
click(settings.reset_demo)
click(nav.open_recipients)
system_back(system.back)
```

Every navigable button carries a stable `Modifier.testTag(...)`. The root
`Scaffold` enables `semantics { testTagsAsResourceId = true }`, so test tags
appear as `resource-id` in UIAutomator dumps. Each screen also emits a
visible-and-readable `screen:<id>` text node (`testTag = screen_marker`).

## Stable identifiers / fidelity notes

- All seed data (`Recipients`, `Account`, `History.seed`) is in-memory and
  deterministic — no random IDs, no timestamps.
- Money is integer cents internally; displayed as `$X.YZ`.
- Seed balance is **$200.00**; seed daily limit is **$500.00**.
- Seeded history: `T-001 -> Bob Singh $25.00 "Lunch"`,
  `T-002 -> Carol Diaz $40.00 "Books"`.
- The two Settings switches (`settings.biometric_unlock`,
  `settings.transfer_notifications`) are flagged `inert: true` in
  `gold/fsm.json` and **do not** affect navigation or guards.
- `settings.reset_demo` resets every piece of FSM-visible state plus the
  inert toggles **and** the balance/history, so a fresh launch and an in-app
  reset produce indistinguishable UI fingerprints.

## Gold artifacts

`gold/` holds evaluator-only ground truth: see [gold/README.md](gold/README.md).
The app does **not** read these files at runtime.

## Scope

No network, no database, no auth, no external images, no runtime permissions,
no integration with the existing Vigil Python pipeline yet.
