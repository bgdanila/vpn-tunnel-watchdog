#!/usr/bin/env python3
"""Django CLI entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    # Make the `daemon` package importable no matter where I run this from.
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dashboard.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django is not installed; run `pip install -r requirements.txt`."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
