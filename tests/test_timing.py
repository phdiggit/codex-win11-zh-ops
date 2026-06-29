from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_win11_zh.cli import main
from codex_win11_zh.timing import append_jsonl, load_timing_input, summarize_timing_metadata


class TimingTests(unittest.TestCase):
    def test_load_timing_input_reads_jsonl_command_records(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "commands.jsonl"
            append_jsonl(
                log_path,
                {
                    "command": "python -m unittest",
                    "result": "passed",
                    "exit_code": 0,
                    "duration_sec": 1.25,
                },
            )

            data = load_timing_input(log_path)
            timing = summarize_timing_metadata(data)

        self.assertEqual(1, len(data["commands"]))
        self.assertEqual("measured", timing["measured_command_time"]["status"])
        self.assertEqual(1.25, timing["measured_command_time"]["duration_sec"])
        self.assertEqual("partial", timing["timing_confidence"])

    def test_timer_cli_finish_writes_task_and_command_timing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "timer.json"
            output = Path(td) / "timing.json"
            command_log = Path(td) / "commands.jsonl"
            append_jsonl(command_log, {"command": "focused", "result": "passed", "exit_code": 0, "duration_sec": 2.5})

            start_rc = main(["timer", "start", "--id", "issue8", "--state", str(state), "--note", "started"])
            mark_rc = main(["timer", "mark", "--id", "issue8", "--state", str(state), "--label", "validation"])
            finish_rc = main(
                [
                    "timer",
                    "finish",
                    "--id",
                    "issue8",
                    "--state",
                    str(state),
                    "--command-log",
                    str(command_log),
                    "--output",
                    str(output),
                    "--note",
                    "finished",
                ]
            )

            data = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, start_rc)
        self.assertEqual(0, mark_rc)
        self.assertEqual(0, finish_rc)
        self.assertEqual("issue8", data["id"])
        self.assertEqual("measured", data["timing"]["measured_task_wall_time"]["status"])
        self.assertEqual("measured", data["timing"]["measured_command_time"]["status"])
        self.assertEqual(2.5, data["timing"]["measured_command_time"]["duration_sec"])
        self.assertEqual("measured", data["timing"]["timing_confidence"])
        self.assertEqual("unknown", data["timing"]["unmeasured_time"]["status"])
        self.assertEqual(["started", "finished"], data["timing"]["qualitative_notes"])
        self.assertEqual("validation", data["marks"][0]["label"])


if __name__ == "__main__":
    unittest.main()
