from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter

from .timing import append_jsonl, build_command_record, duration_sec, utc_now_iso


UTF8_ENV = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
}


def build_runtime_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.update(UTF8_ENV)
    return env


def run_command(command: Sequence[str], *, cwd: str | Path | None = None, log_path: str | Path | None = None, summary: str | None = None) -> int:
    if not command:
        raise ValueError("missing command after --")
    started_at = utc_now_iso()
    started = perf_counter()
    proc = subprocess.run(list(command), cwd=cwd, env=build_runtime_env())
    finished = perf_counter()
    exit_code = int(proc.returncode)
    if log_path is not None:
        record = build_command_record(
            command,
            started_at=started_at,
            finished_at=utc_now_iso(),
            duration=duration_sec(started, finished),
            exit_code=exit_code,
            summary=summary,
        )
        append_jsonl(log_path, record)
    return exit_code
