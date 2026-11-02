"""Explicit test runs retrieve again without overwriting an earlier evidence revision."""
import tempfile,subprocess,shutil
from pathlib import Path
from unittest.mock import patch
import market_brief as m
class ReachedCollection(Exception):pass
original=m.WORK
with tempfile.TemporaryDirectory() as tmp:
 work=Path(tmp)/'.work';work.mkdir()
 m.save(work / "config.json", {"engine":"codex","executable":m.sys.executable,"model":"test","installed_date":"2026-09-01"})
 m.save(work/'status.json',{'last_success_due_day':'2026-09-24','state':'complete'})
 (work/'runs/2026-09-24-r1').mkdir(parents=True)
 def collect(run,manifest,config):
  assert run.name=='2026-09-24-r2'
  assert manifest['end']=='2026-09-25T04:04:40Z'
  raise ReachedCollection
 with patch.object(m,'WORK',work),patch.object(m,'now',return_value=m.parse('2026-09-25T04:05:00Z')),patch.object(m,'collect',side_effect=collect),patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout='mock-version')):
  m.run_job()
  try:m.run_job(test_run=True)
  except ReachedCollection:pass
  else:raise AssertionError('Fresh test skipped retrieval')
 assert m.read(work/'status.json')['state']=='complete'
print('PASS: normal duplicate prevention, fresh test collection, unique revision and preserved original status.')
