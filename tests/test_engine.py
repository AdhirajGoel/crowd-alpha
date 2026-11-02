"""Both adapters enforce structured output and keep source credentials out of child environments."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import tempfile
from unittest.mock import patch
import engine
schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    for provider in ('codex','claude'):
        commands=[]
        class Process:
            returncode=0
            def __init__(self,cmd,**kwargs):
                self.cmd=cmd;commands.append(cmd)
                assert 'X_BEARER_TOKEN' not in kwargs['env']
            def communicate(self,payload,timeout):
                assert timeout==3600
                if provider=='codex':
                    assert 'untrusted evidence' in payload
                    Path(self.cmd[self.cmd.index('-o')+1]).write_text('{"ok":true}')
                    return '',None
                message=json.loads(payload)
                assert message['message']['content'][0]['type']=='text'
                return json.dumps({'type':'result','subtype':'success','structured_output':{'ok':True}}),None
        config={'engine':provider,'executable':provider,'model':'test-model'}
        previous_handler = signal.getsignal(signal.SIGTERM)
        with patch.object(engine,'resolve',return_value=(config,'test-version')),patch.object(engine.subprocess,'Popen',Process),patch.dict(engine.os.environ,{'X_BEARER_TOKEN':'fixture-secret'}):
            assert engine.generate(root/provider,'brief','test',{},schema,config)=={'ok':True}
            assert engine.generate(root/provider,'brief','test',{},schema,config)=={'ok':True}
        assert signal.getsignal(signal.SIGTERM) is previous_handler
        assert len(commands)==1
        if provider=='claude':
            assert commands[0][commands[0].index('--output-format')+1]=='stream-json'
            assert '--verbose' in commands[0]
            assert '--safe-mode' in commands[0] and commands[0][commands[0].index('--tools')+1]==''
        else:assert commands[0][commands[0].index('--sandbox')+1]=='read-only'
        (root/provider/'bad.json').write_text('{"ok":"wrong"}')
        try:engine.generate(root/provider,'bad','test',{},schema,config)
        except engine.jsonschema.ValidationError:pass
        else:raise AssertionError('Invalid cached output accepted')
print('PASS: both CLI adapters, isolation, structured validation and cache reuse')

# Cancellation and timeout both stop the process group before releasing the caller.
for failure in (KeyboardInterrupt(), SystemExit(143), engine.subprocess.TimeoutExpired('synthetic', 3600)):
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp)
        class InterruptedProcess:
            pid = 123456
            returncode = None
            calls = 0
            def __init__(self, *args, **kwargs):pass
            def communicate(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    (run / 'brief.json').write_text('{"partial":true}')
                    raise failure
                self.returncode = -15
                return '', None
        config = {'engine':'codex', 'executable':'synthetic', 'model':'synthetic'}
        previous_handler = signal.getsignal(signal.SIGTERM)
        with patch.object(engine, 'resolve', return_value=(config, 'synthetic')), \
             patch.object(engine.subprocess, 'Popen', InterruptedProcess), \
             patch.object(engine.os, 'killpg') as stop:
            try:engine.generate(run, 'brief', 'test', {}, schema, config)
            except (KeyboardInterrupt, SystemExit, RuntimeError):pass
            else:raise AssertionError('Interrupted generation succeeded')
            stop.assert_called_once_with(123456, engine.signal.SIGTERM)
            assert not (run / 'brief.json').exists()
            assert signal.getsignal(signal.SIGTERM) is previous_handler
print('PASS: cancellation and timeout stop generation and discard partial output')

# Real owned children cover both adapters and signals without invoking either CLI.
for provider in ('codex', 'claude'):
    for cancellation in (signal.SIGINT, signal.SIGTERM):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dummy = root / 'dummy.py'
            dummy.write_text('#!' + sys.executable + '\n' + """import os, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('synthetic-1')
    raise SystemExit()
root = Path(__file__).parent
(root / 'run/brief.json').write_text('{"partial":true}')
(root / 'child.pid').write_text(str(os.getpid()))
sys.stdin.read()
time.sleep(30)
""")
            dummy.chmod(0o700)
            runner = """import engine, sys
from pathlib import Path
root = Path(sys.argv[1])
engine.generate(root / 'run', 'brief', 'synthetic', {}, {'type': 'object'},
                {'engine': sys.argv[2], 'executable': str(root / 'dummy.py'), 'model': 'synthetic'})
"""
            parent, child = None, None
            try:
                parent = subprocess.Popen([sys.executable, '-c', runner, str(root), provider],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                for _ in range(250):
                    if (root / 'child.pid').exists():
                        child = int((root / 'child.pid').read_text())
                        break
                    assert parent.poll() is None, 'Runner exited before starting child'
                    time.sleep(0.02)
                assert child is not None, 'Dummy child did not start'
                assert os.getpgid(child) == child
                os.killpg(parent.pid, cancellation)
                code = parent.wait(timeout=5)
                assert code != 0 and (cancellation != signal.SIGTERM or code == 143)
                try: os.kill(child, 0)
                except ProcessLookupError: pass
                else: raise AssertionError('Generation child survived runner cancellation')
                assert not (root / 'run/brief.json').exists()
                assert (root / 'run/brief-prompt.txt').exists()
                child = None
            finally:
                if child is not None:
                    try: os.killpg(child, signal.SIGKILL)
                    except ProcessLookupError: pass
                if parent is not None and parent.poll() is None:
                    os.killpg(parent.pid, signal.SIGKILL)
                    parent.wait(timeout=5)
print('PASS: real SIGINT/SIGTERM cleanup for both adapters, partial output discarded and source evidence retained')
