"""A missing or broken CLI cannot spend the X budget, including on retries."""
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
import market_brief as m

with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp) / ".work"
    work.mkdir()
    m.save(work / "config.json", {"engine":"codex","executable":m.sys.executable,"model":"test","installed_date":"2026-09-01"})
    with patch.object(m, "WORK", work), \
         patch.object(m, "now", return_value=m.parse("2026-10-04T15:00:00Z")), \
         patch.object(m, "collect") as collect, \
         patch.object(m.subprocess, "run") as cli:
        cli.return_value = subprocess.CompletedProcess([], 0, stdout="mock-version")
        m.run_job(scheduled=True, stage="collect")
        assert collect.call_count == 1
        assert cli.call_args.kwargs["check"] is True
        runtime = next((work / "runs/2026-10-03/runtime").iterdir())
        assert m.read(runtime / "environment.json")["engine_version"] == "mock-version"
        for error in (FileNotFoundError("CLI moved"), subprocess.CalledProcessError(1, "codex")):
            collect.reset_mock()
            cli.side_effect = error
            try:
                m.run_job(scheduled=True, stage="collect")
            except type(error):
                pass
            else:
                raise AssertionError("Broken CLI was accepted")
            collect.assert_not_called()
        # Completed scheduled days remain no-ops without even starting Codex.
        m.save(work / "status.json", {"last_success_due_day": "2026-10-03"})
        cli.reset_mock()
        m.run_job(scheduled=True)
        cli.assert_not_called()

print("PASS: runtime preflight, failed retries cannot collect, completed days stay idle")
