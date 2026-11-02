"""Failed schedule removal preserves recovery state; invalid setup stays actionable."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
import onboarding

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    plist = home / 'Library/LaunchAgents/org.crowd-alpha.daily.plist'
    plist.parent.mkdir(parents=True)
    plist.write_text('synthetic schedule')
    for code in (1, 0):
        with patch.object(Path, 'home', return_value=home), \
             patch.object(sys, 'argv', ['crowd_alpha.py', '--unschedule']), \
             patch.object(onboarding.subprocess, 'run', return_value=subprocess.CompletedProcess([], code)):
            try:onboarding.main()
            except RuntimeError as error:assert code == 1 and 'preserved' in str(error)
            else:assert code == 0
            assert plist.exists() == bool(code)
    fake = home / 'codex'
    fake.write_text('#!/bin/sh\nexit 0\n');fake.chmod(0o700)
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PATH=str(home)+os.pathsep+os.environ['PATH'], CROWD_ALPHA_HOME=str(home/'state'))
    result = subprocess.run([sys.executable, str(repo/'crowd_alpha.py'), '--setup', '--engine', 'codex',
        '--accounts', 'demo_source_1', '--x-monthly-usd', '1', '--x-resource-usd', '.01', '--timezone', 'Pacific'],
        capture_output=True, text=True, env=env)
    assert result.returncode and 'Crowd Alpha setup:' in result.stderr and 'Traceback' not in result.stderr
    assert not (home/'state/config.json').exists()
print('PASS: failed unschedule preserves plist, successful removal cleans it, invalid timezone gives setup error')
