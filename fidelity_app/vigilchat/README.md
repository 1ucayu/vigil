# VigilChat - Fidelity App

A small native Android app used by the [Vigil](../../README.md) project as a
controlled benchmark target. Mirrors the same Kotlin + Jetpack Compose +
Material3 conventions as `vigilmarket`, but exercises the **messaging**
domain: an inbox with seeded threads, per-thread message composition,
attachments, contacts, and two-step destructive confirmations.

## Tooling

- Gradle: 8.7
- Android Gradle Plugin: 8.6.1
- Kotlin: 1.9.24, Compose Compiler: 1.5.14, Compose BOM: 2024.06.00
- compileSdk / targetSdk: 34, minSdk: 26
- JDK: 17+

If `local.properties` is missing, point it at your SDK:

```bash
echo "sdk.dir=$HOME/Library/Android/sdk" > local.properties
```

## Build / install / launch

```bash
cd fidelity_app/vigilchat
./gradlew assembleDebug
./gradlew installDebug
adb -s emulator-5554 shell monkey -p com.vigil.chat -c android.intent.category.LAUNCHER 1
```

### Deterministic clean-launch

```bash
adb -s emulator-5554 shell am start -S -n com.vigil.chat/.MainActivity
```

Or, in-app: open `Settings -> Reset demo` (`testTag = settings.reset_demo`),
which restores screen, current thread, draft, dialog state, per-thread
message lists, and per-thread message-id sequences to the seeded values.

## Implemented states

| State              | Screen marker             | Notes                                                |
|--------------------|---------------------------|------------------------------------------------------|
| `inbox`            | `screen:inbox`            | Entry. Lists 3 seeded threads.                       |
| `thread`           | `screen:thread`           | Template state, parameter `thread_id`.               |
| `attachment_picker`| `screen:attachment_picker`| 3 seeded attachments.                                |
| `contacts`         | `screen:contacts`         | 4 seeded contacts; opening one opens its thread.     |
| `settings`         | `screen:settings`         | `reset_demo`.                                        |
| `message_options`  | `screen:message_options`  | Anchored dialog over `thread`.                       |
| `delete_confirm`   | `screen:delete_confirm`   | Anchored dialog over `thread`.                       |

### Single-authority screen marker

At any moment exactly one node in the composition tree carries
`testTag = "screen_marker"`:

- Non-thread base screens always emit their own marker.
- The `thread` base screen emits its marker only when no dialog is active.
- When `message_options` or `delete_confirm` is active, the dialog emits the
  marker (`screen:message_options` or `screen:delete_confirm`), and the base
  thread screen suppresses its own.

### Message-id rule

- Sending appends `m_<thread_id>_<seq>` where `seq = threadSeqs[thread_id]++`.
- Attaching uses the same scheme; the message body is `[attachment:<name>]`.
- Deletion removes the message from the list but does **not** decrement
  `threadSeqs`. The counter is monotonic for the life of the demo, so deleted
  ids never re-appear.
- `settings.reset_demo` resets both the messages map and the per-thread
  sequence map to the seed values, so a fresh launch and an in-app reset
  produce indistinguishable UI fingerprints.

## Canonical actions (selected)

```
click(inbox.thread_row.alice.open)
input(thread.message_input, "On my way.")
click(thread.send)
click(thread.attach)
click(attachment_picker.item.doc_report)
click(thread.message.m_alice_2.options)
click(message_options.delete)
click(delete_confirm.confirm)
click(delete_confirm.cancel)
click(contacts.contact_row.carol.open)
click(settings.reset_demo)
click(nav.open_inbox)
system_back(system.back)
```

Every navigable control carries a stable `Modifier.testTag(...)`. The root
`Scaffold` enables `semantics { testTagsAsResourceId = true }`, so test tags
appear as `resource-id` in UIAutomator dumps.

## Stable identifiers / fidelity notes

- All seed data (`Threads`, `Contacts`, `Attachments`) is in-memory and
  deterministic - no random ids, no timestamps.
- Dialogs use Material3 `AlertDialog`. They each carry their own
  `<dialog>.confirm` / `<dialog>.cancel` test tags and own the
  `screen_marker` while active.
- System back is implemented via Compose `BackHandler` and is deterministic:
  `delete_confirm -> message_options -> thread`, `attachment_picker -> thread`,
  `contacts/settings -> inbox`, `thread -> inbox`, and from `inbox` it
  finishes the activity.

## Gold artifacts

`gold/` holds evaluator-only ground truth: see [gold/README.md](gold/README.md).
The app does **not** read these files at runtime.

## Scope

No network, no database, no auth, no permissions, no notifications, no
external images, no integration with the existing Vigil Python pipeline yet.
