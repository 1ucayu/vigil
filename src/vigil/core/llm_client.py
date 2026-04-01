"""Unified LLM client wrapper (Anthropic / OpenAI).

Used ONLY during offline stages (state abstraction, DSL generation, Tier 3 evolution).
The online symbolic verifier must NEVER call this client for Tier 1-2.
"""
