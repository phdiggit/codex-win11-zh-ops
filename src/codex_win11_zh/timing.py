from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .encoding import write_json_utf8

DEFAULT_TIMER_STATE_FILE = ".tmp/codex-timer.json"
UNKNOWN_UNMEASURED_NOTE = "manual/reasoning time was not captured separately; do not infer it from wall minus command time"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def duration_sec(start: float, finish: float) -> float:
    return round(max(0.0, finish - start), 3)


def command_to_text(command: Sequence[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in command])


def append_jsonl(path: str | Path, record: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


def build_command_record(
    command: Sequence[str],
    *,
    started_at: str,
    finished_at: str,
    duration: float,
    exit_code: int,
    summary: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": duration,
        "command": command_to_text(command),
        "exit_code": int(exit_code),
        "result": "passed" if int(exit_code) == 0 else "failed",
        "kind": "current_command",
    }
    if summary:
        record["summary"] = summary
    return record


def load_timing_input(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".jsonl":
        return {"commands": _read_jsonl_records(text)}
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return {"commands": _read_jsonl_records(text)}
    if isinstance(data, list):
        return {"commands": [entry for entry in data if isinstance(entry, dict)]}
    if isinstance(data, dict):
        return data
    return {}


def _read_jsonl_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            records.append(value)
    return records


def command_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    commands = data.get("commands", []) if isinstance(data, dict) else []
    return [entry for entry in commands if isinstance(entry, dict)]


def summarize_timing_metadata(data: dict[str, Any]) -> dict[str, Any]:
    timing = data.get("timing") if isinstance(data, dict) else None
    if isinstance(timing, dict):
        summary = _normalize_timing_summary(timing)
    else:
        summary = _unavailable_timing_summary()

    records = command_records(data)
    durations = [_as_float(entry.get("duration_sec")) for entry in records]
    measured_durations = [value for value in durations if value is not None]
    if measured_durations:
        command_time = round(sum(measured_durations), 3)
        summary["measured_command_time"] = {
            "status": "measured",
            "duration_sec": command_time,
            "command_count": len(measured_durations),
        }

    task_status = summary["measured_task_wall_time"]["status"]
    command_status = summary["measured_command_time"]["status"]
    if task_status == "measured" and command_status == "measured":
        confidence = "measured"
    elif task_status == "measured" or command_status == "measured":
        confidence = "partial"
    else:
        confidence = "unavailable"
    summary["timing_confidence"] = confidence
    return summary


def _normalize_timing_summary(timing: dict[str, Any]) -> dict[str, Any]:
    summary = _unavailable_timing_summary()
    task = timing.get("measured_task_wall_time")
    if isinstance(task, dict) and _as_float(task.get("duration_sec")) is not None:
        summary["measured_task_wall_time"] = {
            "status": "measured",
            "duration_sec": _as_float(task.get("duration_sec")),
        }
    command = timing.get("measured_command_time")
    if isinstance(command, dict) and _as_float(command.get("duration_sec")) is not None:
        summary["measured_command_time"] = {
            "status": "measured",
            "duration_sec": _as_float(command.get("duration_sec")),
            "command_count": command.get("command_count"),
        }
    notes = timing.get("qualitative_notes")
    if isinstance(notes, list):
        summary["qualitative_notes"] = [str(note) for note in notes if str(note).strip()]
    return summary


def _unavailable_timing_summary() -> dict[str, Any]:
    return {
        "measured_task_wall_time": {"status": "unavailable", "duration_sec": None},
        "measured_command_time": {"status": "unavailable", "duration_sec": None, "command_count": 0},
        "unmeasured_time": {"status": "unknown", "note": UNKNOWN_UNMEASURED_NOTE},
        "timing_confidence": "unavailable",
        "qualitative_notes": [],
    }


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def load_timer_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    if not state_path.exists():
        return {"timers": {}}
    data = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("timer state must be a JSON object")
    timers = data.setdefault("timers", {})
    if not isinstance(timers, dict):
        raise ValueError("timer state field 'timers' must be an object")
    return data


def save_timer_state(path: str | Path, state: dict[str, Any]) -> None:
    write_json_utf8(path, state)


def start_timer(*, timer_id: str, state_path: str | Path, note: str | None = None, restart: bool = False) -> dict[str, Any]:
    state = load_timer_state(state_path)
    timers = state["timers"]
    existing = timers.get(timer_id)
    if isinstance(existing, dict) and not existing.get("finished_at") and not restart:
        raise ValueError(f"timer is already running: {timer_id}")
    timer = {
        "id": timer_id,
        "started_at": utc_now_iso(),
        "marks": [],
        "qualitative_notes": [],
    }
    if note:
        timer["qualitative_notes"].append(note)
    timers[timer_id] = timer
    save_timer_state(state_path, state)
    return timer


def mark_timer(*, timer_id: str, state_path: str | Path, label: str, note: str | None = None) -> dict[str, Any]:
    state = load_timer_state(state_path)
    timer = _require_timer(state, timer_id)
    mark: dict[str, Any] = {"label": label, "at": utc_now_iso()}
    if note:
        mark["note"] = note
    timer.setdefault("marks", []).append(mark)
    save_timer_state(state_path, state)
    return mark


def finish_timer(
    *,
    timer_id: str,
    state_path: str | Path,
    output_path: str | Path | None = None,
    command_log: str | Path | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    state = load_timer_state(state_path)
    timer = _require_timer(state, timer_id)
    if timer.get("finished_at"):
        raise ValueError(f"timer is already finished: {timer_id}")
    finished_at = utc_now_iso()
    started_at = str(timer["started_at"])
    wall_time = _datetime_duration(started_at, finished_at)
    if note:
        timer.setdefault("qualitative_notes", []).append(note)
    timer["finished_at"] = finished_at
    timer["duration_sec"] = wall_time

    data: dict[str, Any] = {}
    commands: list[dict[str, Any]] = []
    if command_log is not None:
        data = load_timing_input(command_log)
        commands = command_records(data)

    timing = {
        "measured_task_wall_time": {"status": "measured", "duration_sec": wall_time},
        "measured_command_time": _measured_command_time(commands),
        "unmeasured_time": {"status": "unknown", "note": UNKNOWN_UNMEASURED_NOTE},
        "qualitative_notes": list(timer.get("qualitative_notes", [])),
    }
    output = {
        "id": timer_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": wall_time,
        "marks": list(timer.get("marks", [])),
        "timing": summarize_timing_metadata({"timing": timing, "commands": commands}),
    }
    if commands:
        output["commands"] = commands
    timer["timing"] = output["timing"]
    save_timer_state(state_path, state)
    if output_path is not None:
        write_json_utf8(output_path, output)
    return output


def _require_timer(state: dict[str, Any], timer_id: str) -> dict[str, Any]:
    timer = state.get("timers", {}).get(timer_id)
    if not isinstance(timer, dict):
        raise ValueError(f"timer is not started: {timer_id}")
    if "started_at" not in timer:
        raise ValueError(f"timer state is missing started_at: {timer_id}")
    return timer


def _datetime_duration(started_at: str, finished_at: str) -> float:
    start = datetime.fromisoformat(started_at)
    finish = datetime.fromisoformat(finished_at)
    return round(max(0.0, (finish - start).total_seconds()), 3)


def _measured_command_time(commands: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [_as_float(entry.get("duration_sec")) for entry in commands]
    measured = [value for value in durations if value is not None]
    if not measured:
        return {"status": "unavailable", "duration_sec": None, "command_count": 0}
    return {"status": "measured", "duration_sec": round(sum(measured), 3), "command_count": len(measured)}
