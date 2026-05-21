"""Shared project path helpers."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = PROJECT_ROOT / "docs"
OUTPUT_DOCS_DIR = PROJECT_ROOT / "output_docs"

DSL_GRAMMAR_CANDIDATES = (
    OUTPUT_DOCS_DIR / "dsl_grammar.lark",
    DOCS_DIR / "dsl_grammar.lark",
)


def resolve_dsl_grammar_path(grammar_path: str | Path | None = None) -> Path:
    """Return the DSL grammar path, preferring the generated-output directory."""
    if grammar_path is not None:
        return Path(grammar_path)

    for candidate in DSL_GRAMMAR_CANDIDATES:
        if candidate.exists():
            return candidate
    return DSL_GRAMMAR_CANDIDATES[0]


def redirect_docs_output_path(path: str | Path) -> Path:
    """Redirect generated artifacts away from docs/ and into output_docs/."""
    path = Path(path)
    if path.is_absolute():
        try:
            return OUTPUT_DOCS_DIR / path.relative_to(DOCS_DIR)
        except ValueError:
            return path

    if path.parts and path.parts[0] == "docs":
        return OUTPUT_DOCS_DIR.joinpath(*path.parts[1:])
    return path


def resolve_generated_output_path(
    output_path: str | Path | None,
    default_path: str | Path,
) -> Path:
    """Resolve a generated artifact path using output_docs/ as the docs boundary."""
    path = Path(default_path) if output_path is None else Path(output_path)
    return redirect_docs_output_path(path)
