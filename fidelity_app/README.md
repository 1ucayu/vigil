# Vigil Fidelity Apps

This directory holds controlled Android benchmark apps for Vigil. Each
subdirectory is a standalone native Kotlin + Jetpack Compose Gradle project
with its own `gradlew`, package name, gold artifacts, and build outputs.

## Apps

| Directory | Package | Status |
|-----------|---------|--------|
| `vigilmarket/` | `com.vigil.market` | Implemented |
| `vigilbank/` | `com.vigil.bank` | Implemented |
| `vigilchat/` | `com.vigil.chat` | Implemented |
| `vigilclock/` | `com.vigil.clock` | Implemented |

## Common Commands

Run build and install commands from the target app directory:

```bash
cd fidelity_app/vigilmarket
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ./gradlew assembleDebug
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ./gradlew installDebug
adb -s emulator-5554 shell monkey -p com.vigil.market -c android.intent.category.LAUNCHER 1
```

Per-app build shortcuts:

```bash
cd fidelity_app/vigilbank && ./gradlew assembleDebug
cd fidelity_app/vigilchat && ./gradlew assembleDebug
cd fidelity_app/vigilclock && ./gradlew assembleDebug
```

The `gold/` directory inside each app is evaluator-only ground truth. The
running Android app must not read those files.
