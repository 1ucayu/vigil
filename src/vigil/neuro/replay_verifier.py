"""Stage 5: FSM Verification via Replay.

Enumerates bounded-length paths via symbolic execution, converts to test cases,
replays on real device via uiautomator2. Each transition gets a confidence score
(success_count / total_trials).
"""
