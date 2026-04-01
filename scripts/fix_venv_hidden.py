#!/usr/bin/env python3
"""Fix macOS UF_HIDDEN flag on .venv that breaks Python 3.14 .pth processing.

uv creates .venv as a dot-directory, macOS marks it UF_HIDDEN, and Python 3.14's
site.py skips .pth files inside hidden directories. This script clears the flag.

Run after `uv sync`: python3 scripts/fix_venv_hidden.py
"""

import os
import stat
import sys
from pathlib import Path

VENV = Path(__file__).resolve().parent.parent / ".venv"


def main() -> None:
    if not VENV.exists():
        print(f".venv not found at {VENV}")
        sys.exit(1)

    sp = next(VENV.glob("lib/python*/site-packages"), None)
    if sp is None:
        print("site-packages not found")
        sys.exit(1)

    count = 0
    # Clear on .venv dir itself and key paths
    for path in [VENV, VENV / "lib", sp.parent, sp]:
        st = os.stat(path)
        if st.st_flags & stat.UF_HIDDEN:
            os.chflags(path, st.st_flags & ~stat.UF_HIDDEN)
            count += 1

    # Clear on .pth files
    for pth in sp.glob("*.pth"):
        st = os.stat(pth)
        if st.st_flags & stat.UF_HIDDEN:
            os.chflags(pth, st.st_flags & ~stat.UF_HIDDEN)
            count += 1

    if count:
        print(f"Fixed UF_HIDDEN on {count} paths in .venv")
    else:
        print("No UF_HIDDEN flags found, .venv is OK")


if __name__ == "__main__":
    main()
