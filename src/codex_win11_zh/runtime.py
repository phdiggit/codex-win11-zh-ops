from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path


UTF8_ENV = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
}


def build_runtime_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.update(UTF8_ENV)
    return env


def run_command(command: Sequence[str], *, cwd: str | Path | None = None) -> int:
    if not command:
        raise ValueError("missing command after --")
    proc = subprocess.run(list(command), cwd=cwd, env=build_runtime_env())
    return int(proc.returncode)
