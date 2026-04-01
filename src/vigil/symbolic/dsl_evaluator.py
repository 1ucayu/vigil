"""Tier 2: DSL Semantic Verification (< 15 ms).

Evaluates DSL guard expressions against the current screen state using the Lark
parser (docs/dsl_grammar.lark). Guard templates are cached offline; parameters
are bound at runtime.
"""
