"""Offline check for on-demand retrieval, coverage, and isolation from daily briefs."""
import fcntl
import tempfile
from pathlib import Path
from unittest.mock import patch
import market_brief as m
import query_x


with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp)
    m.save(work / 'config.json', {'x_resource_usd': 0.005, 'x_monthly_usd': 30})
    for name in ('status.json', 'memory.json', 'live/current.json'):
        m.save(work / name, {'unchanged': name})
    before = {name: (work / name).read_bytes() for name in ('status.json', 'memory.json', 'live/current.json')}
    author = '1000000000000000007'
    calls = []

    def request(url, headers, params, record):
        calls.append((url, params))
        if url.endswith('/tweets'):
            data = {'data': [{'id': '2', 'author_id': 'outside-roster', 'text': 'Referenced context'}]}
        elif params.get('next_token'):
            data = {'meta': {'result_count': 0}}
        else:
            assert params['query'] == '(from:demo_source_7)'
            assert params['start_time'] == '2026-09-28T10:59:40Z'
            assert params['end_time'] == '2026-09-28T11:59:40Z'
            data = {'data': [{'id': '1', 'author_id': author, 'created_at': '2026-09-28T11:30:00Z',
                             'text': 'A new view', 'referenced_tweets': [{'id': '2', 'type': 'quoted'},
                                                                         {'id': '3', 'type': 'replied_to'}]}],
                    'meta': {'next_token': 'page2', 'result_count': 1}}
        m.save(record, data)
        return data

    with patch.object(m, 'WORK', work), patch.object(m, 'now', return_value=m.parse('2026-09-28T12:00:00Z')), \
         patch.object(m, 'secret', return_value='test-token'), patch.object(m, 'request', side_effect=request):
        run = query_x.query(1, ['@demo_source_7'])
        bundle = m.read(run / 'posts.json')
        assert bundle['coverage'] == {'demo_source_7': 1}
        assert bundle['context'][0]['id'] == '2'
        assert bundle['unavailable_context_ids'] == ['3']
        assert len(calls) == 3
        assert m.read(work / 'x-budget.json') == {'2026-09': 0.01}
        assert m.read(run / 'manifest.json')['state'] == 'complete'
        for hours, accounts in ((0, None), (168, None), (float('nan'), None), (1, ['unknown'])):
            try:
                query_x.query(hours, accounts)
            except ValueError:
                pass
            else:
                raise AssertionError('Invalid input was accepted')
        assert len(calls) == 3
        with (work / 'run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                query_x.query()
            except RuntimeError as exc:
                assert 'running' in str(exc)
            else:
                raise AssertionError('Concurrent collection was accepted')
        with patch.object(m, 'request', return_value={'errors': [{'detail': 'partial search'}]}):
            try:
                query_x.query()
            except RuntimeError:
                pass
            else:
                raise AssertionError('Partial search reported success')
        assert any(m.read(p)['state'] == 'failed' for p in (work / 'live/queries').glob('*/manifest.json'))
        assert all((work / name).read_bytes() == content for name, content in before.items())
        assert not (work / 'runs').exists()
        assert not list(work.glob('*.pdf'))
print('PASS: pagination, context gaps, account/window validation, shared budget/lock, failure reporting, and daily-state isolation.')
