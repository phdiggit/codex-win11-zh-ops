from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import run_plan_foreground
from .stdio import configure_utf8_stdio


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Internal codex-win agent supervisor worker")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    run_plan_foreground(config, launched_in_background=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
