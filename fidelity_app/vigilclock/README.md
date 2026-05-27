# VigilClock — Fidelity App

A small native Android app used by the [Vigil](../../README.md) project as a
controlled benchmark target alongside [VigilMarket](../vigilmarket/). Vigil's
offline pipeline (APK static priors, runtime UI XML, screenshots, action
traces, replay) is exercised against this app to test whether it can
faithfully construct and validate an FSM
`M_A = <S, s0, Sigma, delta, Gamma, I, rho>` for a time-driven UI.

The app is intentionally native and deterministic. It does **not** read any
wall-clock API: no `System.currentTimeMillis()`, no `LocalTime.now()`, no
`Calendar`, no `Instant`. The only time signal is a relative monotonic
counter advanced by `kotlinx.coroutines.delay(100L)` while the screen is in a
RUNNING state. The counter resets to 0 on every transition into RUNNING and
freezes on pause / idle / done.

## Why a separate fidelity app

VigilMarket already covers product-catalog / cart / checkout flows. VigilClock
adds **time-driven states** (`timer_running`, `timer_paused`, `timer_done`,
`stopwatch_running`, `stopwatch_paused`) plus **volatile readouts**
(`timer.remaining`, `stopwatch.elapsed`) whose text changes every tick while
the screen marker stays static. That combination stresses the FSM builder's
ability to keep a stable state fingerprint in the presence of fast-changing
text and to evaluate guards that reference relative-millisecond quantities.

## Tooling

Identical to VigilMarket — see `../vigilmarket/README.md` for full provenance
and SHA-256 information. Same AGP 8.6.1, Kotlin 1.9.24, Compose BOM
2024.06.00, Compose compiler 1.5.14, compileSdk/targetSdk 34, minSdk 26, JDK
17, Gradle 8.7.

If `local.properties` is missing, point it at your SDK:

```bash
echo "sdk.dir=$HOME/Library/Android/sdk" > local.properties
```

## Build / install / launch

```bash
cd fidelity_app/vigilclock
./gradlew assembleDebug
./gradlew installDebug
adb -s emulator-5554 shell monkey -p com.vigil.clock -c android.intent.category.LAUNCHER 1
```

### Deterministic clean-launch flow

```bash
adb -s emulator-5554 shell am start -S -n com.vigil.clock/.MainActivity
```

Or, in-app: open `Settings -> Reset demo` (`testTag = settings.reset_demo`),
which restores the seeded alarms and clears every timer / stopwatch counter.
A fresh launch and an in-app reset produce the same UI fingerprint.

## Implemented states

| State              | Screen marker             | Notes                                |
|--------------------|---------------------------|--------------------------------------|
| `alarm_list`       | `screen:alarm_list`       | Entry. Lists 3 seeded alarms.        |
| `alarm_edit`       | `screen:alarm_edit`       | Hour/minute +/- (24h/60m wrap).      |
| `timer_setup`      | `screen:timer_setup`      | 4 preset duration chips.             |
| `timer_running`    | `screen:timer_running`    | Tick = `delay(100ms)`. Volatile `timer.remaining`. |
| `timer_paused`     | `screen:timer_paused`     | Tick frozen. Volatile `timer.remaining`. |
| `timer_done`       | `screen:timer_done`       | `timer.remaining_ms == 0` invariant. |
| `stopwatch_idle`   | `screen:stopwatch_idle`   | `stopwatch.elapsed_ms == 0` invariant. |
| `stopwatch_running`| `screen:stopwatch_running`| Volatile `stopwatch.elapsed`, lap allowed. |
| `stopwatch_paused` | `screen:stopwatch_paused` | Tick frozen. Volatile `stopwatch.elapsed`. |
| `settings`         | `screen:settings`         | `settings.reset_demo`.               |

There are no dialog states in this app.

## Bottom navigation

A Material3 bottom navigation bar is visible on every state. Its global actions
are tagged `nav.open_alarms`, `nav.open_timer`, `nav.open_stopwatch`,
`nav.open_settings`. `nav.open_timer` returns to whichever of
`timer_setup / running / paused / done` is current (so an in-progress timer is
preserved), and likewise for `nav.open_stopwatch`. Encoded in
`gold/fsm.json` under `global_navigation`.

## Back routing

System back is implemented via Compose `BackHandler` and is deterministic.
Only `alarm_edit` consumes back (pops to `alarm_list`); from every other
top-level tab, back returns false and the activity exits to the launcher. The
timer / stopwatch monotonic counter is intentionally not preserved across an
activity exit — the in-app reset/return path is `timer.reset` /
`stopwatch.reset`, not system back.

## Canonical actions (selected)

```
click(nav.open_timer)
click(timer.duration.d_5m)
click(timer.start)
click(timer.pause)
click(timer.resume)
click(timer.reset)
click(timer.fast_forward_done)
click(stopwatch.start)
click(stopwatch.lap)
click(alarm.toggle.a_morning)
click(alarm.edit.a_lunch)
click(alarm_edit.hour.increment)
click(alarm_edit.minute.decrement)
click(alarm_edit.save)
click(alarm_edit.cancel)
click(settings.reset_demo)
system_back(system.back)
```

Every navigable element carries a stable `Modifier.testTag(...)`. The root
`Scaffold` enables `semantics { testTagsAsResourceId = true }`, so test tags
appear as `resource-id` in UIAutomator dumps. Each screen emits a
visible-and-readable `screen:<id>` text node (`testTag = screen_marker`) in
its own composable, separate from any volatile readout.

## Stable identifiers / fidelity notes

- All seed data (`Alarms`, `TimerDurations`) is in-memory and deterministic —
  no random IDs, no timestamps.
- Time is integer milliseconds internally. Display is via the pure
  `formatMs(ms: Long)` helper (`mm:ss.t` under one hour, otherwise `hh:mm:ss`).
- No wall-clock APIs are used anywhere; verified by `grep`.
- `timer.fast_forward_done` is provided so tasks can complete the timer
  without a real wait. Visible on `timer_running` and `timer_paused` only.

## Gold artifacts

`gold/` holds evaluator-only ground truth: see [gold/README.md](gold/README.md).
The app does **not** read these files at runtime.

## Scope

No network, no database, no auth, no notifications, no permissions, no
external images, no integration with the existing Vigil Python pipeline yet.
