from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from codex_win11_zh.cli import main
from codex_win11_zh.runtime import build_runtime_env


class RuntimeTests(unittest.TestCase):
    def test_runtime_env_forces_utf8(self) -> None:
        env = build_runtime_env({"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp936"})

        self.assertEqual("1", env["PYTHONUTF8"])
        self.assertEqual("utf-8", env["PYTHONIOENCODING"])

    def test_run_cli_propagates_utf8_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "env.json"
            code = (
                "import json, os, pathlib, sys; "
                "pathlib.Path(sys.argv[1]).write_text("
                "json.dumps({'PYTHONUTF8': os.environ.get('PYTHONUTF8'), "
                "'PYTHONIOENCODING': os.environ.get('PYTHONIOENCODING')}), "
                "encoding='utf-8')"
            )

            rc = main(["run", "--", sys.executable, "-c", code, str(output)])

            self.assertEqual(0, rc)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}, data)

    def test_run_cli_preserves_exit_code(self) -> None:
        rc = main(["run", "--", sys.executable, "-c", "import sys; sys.exit(7)"])

        self.assertEqual(7, rc)

    def test_run_cli_appends_timing_jsonl_and_preserves_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "codex-commands.jsonl"

            rc = main(["run", "--log", str(log_path), "--summary", "expected failure", "--", sys.executable, "-c", "import sys; sys.exit(7)"])

            self.assertEqual(7, rc)
            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(lines))
            record = json.loads(lines[0])
            self.assertEqual(7, record["exit_code"])
            self.assertEqual("failed", record["result"])
            self.assertEqual("expected failure", record["summary"])
            self.assertGreaterEqual(record["duration_sec"], 0)
            self.assertIn("started_at", record)
            self.assertIn("finished_at", record)
            self.assertIn(sys.executable, record["command"])

    def test_run_cli_appends_multiple_jsonl_records(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "codex-commands.jsonl"

            first = main(["run", "--log", str(log_path), "--", sys.executable, "-c", "pass"])
            second = main(["run", "--log", str(log_path), "--", sys.executable, "-c", "pass"])

            self.assertEqual(0, first)
            self.assertEqual(0, second)
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(2, len(records))
            self.assertEqual(["passed", "passed"], [record["result"] for record in records])


if __name__ == "__main__":
    unittest.main()
