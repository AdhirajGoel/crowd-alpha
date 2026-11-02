"""Daily gaps remain queued; collection survives model failure and retries recover."""
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
import market_brief as m

with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp) / '.work'
    work.mkdir()
    m.save(work / 'config.json', {'installed_date': '2026-09-28'})
    m.save(work / 'status.json', {'last_success_due_day': '2026-10-03', 'state': 'complete'})
    record = work / 'provenance/2026-10-03'
    m.save(record / 'record.json', {'pdf': '2026-10-03-market-brief.pdf', 'window': {'due_day': '2026-10-03'}})
    (Path(tmp) / '2026-10-03-market-brief.pdf').write_bytes(b'published')
    (record / '2026-10-03-market-brief.pdf').write_bytes(b'published')
    at = m.parse('2026-10-04T15:00:00Z')
    with patch.object(m, 'WORK', work), patch.object(m, 'ROOT', Path(tmp)), patch.object(m.provenance, 'verify'):
        assert m.pending_days({'installed_date': '2026-09-28'}, at) == [
            '2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02']
    with patch.object(m, 'WORK', work), patch.object(m, 'ROOT', Path(tmp)), \
         patch.object(m.provenance, 'verify', side_effect=RuntimeError('damaged archive')):
        assert m.pending_days({'installed_date': '2026-10-03'}, at) == ['2026-10-03']
    assert m.daily_manifest('2026-11-01')['end'] == '2026-11-02T00:59:40Z'
    assert m.daily_manifest('2026-11-01')['start'] == '2026-10-31T23:59:40Z'
    with patch.object(m, 'WORK', work), patch.object(m, 'ROOT', Path(tmp)), \
         patch.object(m.provenance, 'verify'), patch.object(m, 'publish_record') as restore:
        (Path(tmp) / '2026-10-03-market-brief.pdf').write_bytes(b'corrupted')
        assert m.pending_days({'installed_date': '2026-10-03'}, at) == []
        restore.assert_called_once_with(record, '2026-10-03')

    with patch.object(m, 'WORK', work), patch.object(m, 'now', return_value=at), \
         patch.object(m, 'initialize'), patch.object(m, 'run_pending', side_effect=RuntimeError('runner fault')), \
         patch.object(m, 'notify_failure', return_value=True) as notify:
        for mode in ('--backfill', '--scheduled', '--scheduled'):
            with patch.object(m.sys, 'argv', ['market_brief.py', mode]):
                try: m.main()
                except SystemExit as e: assert e.code == 1
                else: raise AssertionError('Fatal runner fault was not reported')
            assert notify.call_count == (0 if mode == '--backfill' else 1)
        with patch.object(m, 'now', return_value=at + m.timedelta(days=1)), \
             patch.object(m.sys, 'argv', ['market_brief.py', '--scheduled']):
            try: m.main()
            except SystemExit as e: assert e.code == 1
        assert notify.call_count == 2

    days = ['2026-10-01', '2026-10-02']
    complete = set()
    events = []
    broken = [True]
    def collect(run, manifest, config):
        assert m.read(work / 'status.json')['state'] == 'recovering'
        if not (run / 'posts.json').exists():
            events.append(('capture', run.name))
            m.save(run / 'posts.json', {'posts': [], 'context': []})
    def job(day):
        events.append(('report', day))
        if broken[0]:
            raise RuntimeError('Codex unavailable')
        complete.add(day)
    with patch.object(m, 'WORK', work), patch.object(m, 'now', return_value=at), \
         patch.object(m, 'pending_days', side_effect=lambda *_: [d for d in days if d not in complete]), \
         patch.object(m, 'prior_record', return_value=None), patch.object(m, 'collect', side_effect=collect), \
         patch.object(m.macro_data, 'collect'), patch.object(m, 'run_job', side_effect=job), \
         patch.object(m, 'notify_failure', return_value=True) as notify:
        assert not m.run_pending(force=True)
        notify.assert_not_called()  # Supervised recovery does not emit desktop alerts.
        assert events[:2] == [('capture', d) for d in days]
        assert m.read(work / 'status.json')['pending_days'] == days
        assert m.read(work / 'status.json')['state'] == 'needs_attention'
        events.clear()
        assert not m.run_pending() and not events  # Backoff prevents a retry storm.
        with patch.object(m, 'now', return_value=at + m.timedelta(minutes=31)):
            assert not m.run_pending()
            assert notify.call_count == 2
        with patch.object(m, 'now', return_value=at + m.timedelta(hours=2)):
            assert not m.run_pending()
            assert notify.call_count == 2  # Identical scheduled failures notify once a day.
        events.clear()
        broken[0] = False
        assert m.run_pending(force=True)
        assert events == [('report', d) for d in days]  # Sources were cached once.
        assert m.read(work / 'queue.json')['pending_days'] == []
        assert m.read(work / 'status.json')['last_success_due_day'] == '2026-10-03'
        events.clear()
        assert m.run_pending() and not events

    draft = work / 'runs/rejected'
    m.save(draft / 'posts.json', {'evidence': 'keep'})
    m.save(draft / 'brief.json', {'invalid': True})
    (draft / 'brief-agent.log').write_text('original model response')
    m.reject_cached_draft(draft, RuntimeError('A factual provenance claim has no source'))
    assert not (draft / 'brief.json').exists()
    assert len(list((draft / 'rejected-drafts').glob('*/brief.json'))) == 1
    assert m.read(draft / 'posts.json') == {'evidence': 'keep'}
    assert 'no source' in m.read(draft / 'brief-feedback.json')['error']

    original = '/old/location/codex'
    moved = '/new/location/codex'
    with patch.object(m.engine.shutil, 'which', side_effect=lambda value: moved if value == 'codex' else None), \
         patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='new-version')):
        config, version = m.resolve_codex({'engine': 'codex', 'executable': original})
        assert config['executable'] == moved and version == 'new-version'

print('PASS: gaps below latest success, capture before models, persistent retries/backoff, cache reuse, no duplicate reports, moved executable recovery')
