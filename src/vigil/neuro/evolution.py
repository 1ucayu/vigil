"""Tier 3: Online Micro-Evolution engine.

Handles previously unseen UI states at runtime. Uses structural similarity matching
to inherit guards from similar known states (inherit_and_bind). Falls back to LLM
generation only when no similar state exists. Results are cached back into the FSM
bundle for monotonically increasing coverage.
"""
