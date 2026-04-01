"""Stage 2: State Abstraction (structural fingerprinting + LLM-assisted grouping).

Maps raw screens to abstract states. Phase 1 uses rule-based structural fingerprinting
(hash of class_name, resource_id, depth, interactability). Phase 2 uses LLM for
ambiguous cases.
"""
