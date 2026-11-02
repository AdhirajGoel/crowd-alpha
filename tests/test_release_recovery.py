"""Offline full-pipeline regressions for daily coverage, retries and historical notes."""
import copy
import json
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from datetime import timedelta
import market_brief as m
import demo

class SyntheticJob:
    def __init__(self, name, clock):
        self.work = BASE / name
        self.work.mkdir()
        self.clock = m.parse(clock)
        self.bundle, self.market, self.result, self.selection = demo.fixtures()
        self.config = {'engine': 'codex', 'model': 'synthetic', 'executable': 'synthetic',
                       'installed_date': self.clock.astimezone(m.PT).date().isoformat(),
                       'accounts': {'1': 'demo_source_1', '2': 'demo_source_2'},
                       'timezone': 'America/Los_Angeles', 'hour': 17,
                       'x_monthly_usd': 30, 'x_resource_usd': 0.005}
        m.save(self.work / 'config.json', self.config)
        self.calls = 0

    def collect(self, run, manifest, config):
        data = copy.deepcopy(self.bundle)
        data.update(start=manifest['start'], end=manifest['end'])
        for post in data['posts']:
            post['created_at'] = m.stamp(m.parse(manifest['end']) - timedelta(hours=1))
        m.save(run / 'posts.json', data)
        m.save(run / 'triage.json', self.selection)
        return data

    def market_data(self, run, symbols, manifest):
        data = copy.deepcopy(self.market)
        data['as_of'] = manifest['end']
        m.save(run / 'market.json', data)
        return data

    def macro(self, run, cutoff, previous=None, **kwargs):
        data = {'as_of': cutoff.isoformat(), 'sources': [], 'errors': [], 'fingerprints': {},
                'previous_record': previous or {}, 'limitations': 'Synthetic offline probe.'}
        m.save(run / 'macro.json', data)
        return data

    def process(self, cmd, **kwargs):
        owner = self
        output = Path(cmd[cmd.index('-o') + 1])
        class FakeProcess:
            returncode = 0
            def communicate(self, payload, timeout):
                owner.calls += 1
                result = copy.deepcopy(owner.result)
                if getattr(owner, 'bad_first', False) and owner.calls == 1:
                    result['evidence_ids'].append('not-a-supplied-id')
                output.write_text(json.dumps(result))
                return ('synthetic engine output', None)
        return FakeProcess()

    def __enter__(self):
        self.stack = ExitStack()
        for obj, name, kwargs in [
            (m, 'WORK', {'new': self.work}), (m, 'ROOT', {'new': self.work / 'reports'}),
            (m, 'ACCOUNTS', {'new': self.config['accounts']}),
            (m, 'now', {'side_effect': lambda: self.clock}),
            (m, 'collect', {'side_effect': self.collect}),
            (m, 'market_data', {'side_effect': self.market_data}),
            (m.macro_data, 'collect', {'side_effect': self.macro}),
            (m, 'resolve_codex', {'return_value': (self.config, 'offline synthetic')}),
            (m.engine, 'resolve', {'return_value': (self.config, 'offline synthetic')}),
            (m.engine.subprocess, 'Popen', {'side_effect': self.process}),
            (m, 'notify_failure', {'return_value': False}),
        ]:
            self.stack.enter_context(patch.object(obj, name, **kwargs))
        (self.work / 'reports').mkdir()
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

with tempfile.TemporaryDirectory() as tmp:
    BASE = Path(tmp)
    with SyntheticJob('windows', '2026-10-30T17:00:00Z') as job:
        m.run_job()
        first = m.read(job.work / 'provenance/2026-10-29/source/manifest.json')
        assert all(first[key] == value for key, value in m.daily_manifest('2026-10-29').items())
        m.run_job(test_run=True)
        ad_hoc = m.read(job.work / 'provenance/2026-10-30-r1/record.json')
        assert ad_hoc['window']['ad_hoc']
        assert ad_hoc['pdf'] == '2026-10-30-r1-market-brief.pdf'
        assert m.read(job.work / 'status.json')['last_success_due_day'] == '2026-10-29'
        job.clock = m.parse('2026-10-31T00:01:00Z')
        assert m.pending_days(job.config, job.clock) == ['2026-10-30']
        assert m.run_pending(force=True)
        following = m.read(job.work / 'provenance/2026-10-30/source/manifest.json')
        assert following['start'] == first['end']
        # A newer ad-hoc revision cannot hide or overwrite the completed daily archive.
        m.run_job(test_run=True)
        assert m.pending_days(job.config, job.clock) == []
        canonical = job.work / 'reports/2026-10-30-market-brief.pdf'
        canonical.unlink()
        assert m.pending_days(job.config, job.clock) == [] and canonical.exists()
        assert m.read(job.work / 'status.json')['provenance'].endswith('/2026-10-30/index.html')
    with SyntheticJob('retry', '2026-10-31T00:01:00Z') as job:
        job.bad_first = True
        try:m.run_job()
        except RuntimeError as error:assert 'evidence' in str(error).lower()
        else:raise AssertionError('Invalid evidence published')
        run = job.work / 'runs/2026-10-30'
        assert not (run / 'brief.json').exists()
        assert len(list((run / 'rejected-drafts').glob('*/brief.json'))) == 1
        assert (run / 'posts.json').exists()
        m.run_job()
        assert job.calls == 2
        assert m.read(job.work / 'status.json')['state'] == 'complete'
    with SyntheticJob('history', '2026-10-31T00:01:00Z') as job:
        old = {'id':'old', 'recorded_at':'2026-10-29T18:00:00Z', 'supersedes':[],
               'author':'user', 'kind':'user_hypothesis', 'text':'Synthetic earlier thesis.'}
        later = {**old, 'id':'later', 'recorded_at':'2026-10-30T03:00:00Z', 'supersedes':['old']}
        for note in (old, later):m.save(job.work / 'live/conversations' / (note['id'] + '.json'), note)
        m.run_job(day='2026-10-29')
        assert m.read(job.work / 'runs/2026-10-29/live-before.json')['discussion_notes'] == [old]
        assert m.live_memory.context(job.work)['discussion_notes'] == [later]
print('PASS: canonical daily coverage, separate rolling revisions, rejected draft recovery, historical note supersession')
