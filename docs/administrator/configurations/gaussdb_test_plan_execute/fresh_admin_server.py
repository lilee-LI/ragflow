#!/usr/bin/env python3
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

from werkzeug import serving


PROJECT_ROOT = Path(__file__).resolve().parents[4]
ADMIN_SERVER_DIR = PROJECT_ROOT / "admin" / "server"
ADMIN_SERVER = ADMIN_SERVER_DIR / "admin_server.py"


def main() -> None:
    port = int(os.environ.get("ADMIN_PORT", "9381"))
    original_run_simple = serving.run_simple

    def run_simple(*args, **kwargs):
        positional = list(args)
        if len(positional) >= 2:
            positional[1] = port
        else:
            kwargs["port"] = port
        return original_run_simple(*positional, **kwargs)

    serving.run_simple = run_simple
    sys.path.insert(0, str(ADMIN_SERVER_DIR))
    runpy.run_path(str(ADMIN_SERVER), run_name="__main__")


if __name__ == "__main__":
    main()
