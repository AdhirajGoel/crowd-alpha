"""Uncertain external evidence cannot become verified context, coverage or prices."""
import copy
from pathlib import Path
import tempfile
from unittest.mock import patch
import market_brief as m
import demo


def seal(record):
    m.save(record / 'integrity.json', {'sha256': {
        p.relative_to(record).as_posix(): m.provenance.digest(p)
        for p in record.rglob('*') if p.is_file() and p.name != 'integrity.json'}})


bundle, market, final, selection = demo.fixtures()
manifest = {'start': '2026-10-31T00:59:40Z', 'end': '2026-11-01T00:59:40Z'}
raw = {'id': '3', 'author_id': '1', 'created_at': '2026-10-31T18:00:00Z', 'text': 'Synthetic reply',
       'referenced_tweets': [{'id': i, 'type': 'quoted'} for i in ('1', '2')]}
search = {'data': [raw], 'meta': {'result_count': 1}}
with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'WORK', Path(tmp)), \
     patch.object(m, 'ACCOUNTS', {'1': 'demo_source_1', '2': 'demo_source_2'}):
    work = Path(tmp)
    record = work / 'provenance/2026-10-30'
    m.save(record / 'source/posts.json', bundle)
    seal(record)
    with patch.object(m, 'secret', side_effect=AssertionError('No credentials on valid cache hit')):
        context, reused = m.cached_context(work / 'runs/new', {'1', '2'}, manifest['end'])
        assert set(context) == {'1', '2'} and len(reused) == 1
    corrupted = copy.deepcopy(bundle)
    corrupted['posts'][0]['text'] = 'Altered synthetic evidence'
    m.save(record / 'source/posts.json', corrupted)
    original_bytes = (record / 'source/posts.json').read_bytes()
    alternate = work / 'provenance/2026-10-29'
    m.save(alternate / 'source/posts.json', {**bundle, 'posts': bundle['posts'][:1]})
    seal(alternate)
    run = work / 'runs/new'
    m.save(run / 'x-page-000.json', search)
    lookup = {'data': [{'id': '2', 'author_id': '2', 'created_at': raw['created_at'], 'text': 'Fresh context'}]}
    with patch.object(m, 'secret', return_value='synthetic'), \
         patch.object(m.XReader, 'get', return_value=lookup) as fetch:
        result = m.collect(run, manifest, {})
        assert fetch.call_count == 1 and fetch.call_args.args[1]['ids'] == '2'
        assert {p['id']: p['text'] for p in result['context']} == {'1': bundle['posts'][0]['text'], '2': 'Fresh context'}
        assert result['context_reuse'][0]['source_file'] == str(alternate / 'source/posts.json')
    assert (record / 'source/posts.json').read_bytes() == original_bytes
    # Verify the archive before parsing corrupted metadata; raw query caches stay usable.
    (record / 'source/posts.json').write_text('invalid json')
    m.save(work / 'live/queries/old/posts.json', {**bundle, 'posts': bundle['posts'][1:]})
    assert set(m.cached_context(work / 'runs/other', {'1', '2'}, manifest['end'])[0]) == {'1', '2'}

    image_record = work / 'provenance/image'
    image = image_record / 'source/images/chart.jpg'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'original synthetic photo')
    m.save(image_record / 'source/manifest.json', {'end': bundle['end']})
    photos = {'photos': [{'path': str(image), 'media_key': 'chart', 'post_ids': ['1'], 'sha256': m.provenance.digest(image)}]}
    m.save(image_record / 'source/images.json', photos)
    seal(image_record)
    destination = work / 'runs/photos'
    m.save(destination / 'manifest.json', manifest)
    assert 'chart' in m.cached_photos(destination, {'chart': {'3'}})[0]
    image.write_bytes(b'altered synthetic photo')
    photos['photos'][0]['sha256'] = m.provenance.digest(image)
    m.save(image_record / 'source/images.json', photos)
    assert m.cached_photos(destination, {'chart': {'3'}}) == ({}, [])
print('PASS: verified archive reuse, corrupt text/photo metadata rejection, alternate caches and exact missing-ID retrieval')

config = {'x_resource_usd': 0.005, 'x_monthly_usd': 30}
bad_searches = [None, {}, {'meta': {'result_count': 3}, 'data': []}, [],
                {'meta': {'result_count': True}}, {'meta': {'result_count': '0'}},
                {'meta': {'result_count': 0}, 'data': {}},
                {'meta': {'result_count': 0}, 'includes': {'media': {}}},
                {'meta': {'result_count': 0}, 'errors': {}},
                {'meta': {'result_count': 0, 'next_token': 1}},
                {'meta': {'result_count': 1}, 'data': [{'id': '1'}]},
                {'errors': [{'detail': 'partial search'}]}]
with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'WORK', Path(tmp)), \
     patch.object(m, 'ACCOUNTS', {'1': 'demo_source_1', '2': 'demo_source_2'}), \
     patch.object(m, 'now', return_value=m.parse('2026-11-01T02:00:00Z')), \
     patch.object(m, 'secret', return_value='synthetic'):
    work = Path(tmp)
    ledger = work / 'x-budget.json'
    for index, payload in enumerate(bad_searches):
        m.save(ledger, {})
        run = work / str(index)
        def request(url, headers, params, record):
            m.save(record, payload)
            m.save(record.with_suffix('.request.json'), {'synthetic': True})
            return payload
        with patch.object(m, 'request', side_effect=request) as fetch:
            try: m.collect(run, manifest, config)
            except RuntimeError: pass
            else: raise AssertionError('Malformed search accepted')
            assert fetch.call_count == 1 and m.read(ledger) == {'2026-10': 0.5}
            assert not (run / 'posts.json').exists() and not (run / 'x-page-000.json').exists()
            rejected = list((run / 'rejected-responses').glob('*/x-page-000.json'))
            assert len(rejected) == 1 and m.read(rejected[0]) == payload
            assert m.read(rejected[0].with_suffix('.request.json')) == {'synthetic': True}
        # An ordinary retry replaces only the rejected page without releasing uncertain spending.
        with patch.object(m, 'request', return_value={'meta': {'result_count': 0}}) as fetch:
            assert m.collect(run, manifest, config)['posts'] == [] and fetch.call_count == 1
            assert m.read(ledger) == {'2026-10': 0.5}
    cached = work / 'cached'
    cached.mkdir()
    with patch.object(m, 'secret', side_effect=AssertionError('No credentials for cached validation')):
        for index, raw_json in enumerate(('null', 'invalid json', '{"meta":{"result_count":3},"data":[]}'), 1):
            (cached / 'x-page-000.json').write_text(raw_json)
            try: m.collect(cached, manifest, config)
            except RuntimeError: pass
            else: raise AssertionError('Malformed cached search accepted')
            rejected = list((cached / 'rejected-responses').glob('*/x-page-000.json'))
            assert len(rejected) == index and any(p.read_text() == raw_json for p in rejected)
        m.save(cached / 'x-page-000.json', {'meta': {'result_count': 0}})
        assert m.collect(cached, manifest, config)['posts'] == []
    reader = m.XReader(config)
    for endpoint, payload, expected in [
        ('/tweets/search/recent', {'data': [{**raw, 'referenced_tweets': []}], 'meta': {'result_count': 1}}, 0.005),
        ('/users/by', {'data': [{'id': '1', 'username': 'demo_source_1'}]}, 0.005),
        ('/tweets', {'data': [{'id': '1'}], 'errors': [{'detail': 'second post unavailable'}]}, 0.005),
        ('/tweets', {'errors': [{'detail': 'post unavailable'}]}, 0.0),
        ('/tweets', {'data': [{'id': '1'}], 'includes': {'media': [{'media_key': 'chart'}]}}, 0.01)]:
        m.save(ledger, {})
        with patch.object(m, 'request', return_value=payload):
            assert reader.get(endpoint, {}, 100, work / 'lookup.json') == payload
            assert m.read(ledger) == {'2026-10': expected}
print('PASS: malformed fresh/cached X envelopes retain reservations and quarantine evidence; valid empty/search/lookup/media envelopes settle correctly')

rows = [{'t': '2026-06-01T04:00:00Z', 'c': 50}, *market['bars']['SPY'],
        {'t': '2026-10-30T23:59:50Z', 'c': 150}, {'t': '2026-11-02T05:00:00Z', 'c': 200}]
with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'secret', return_value='synthetic'), \
     patch.object(m, 'now', return_value=m.parse('2026-11-04T00:00:00Z')):
    work = Path(tmp)
    for label, cutoff, expected in [('before-close', '2026-10-30T17:00:00Z', market['bars']['SPY'][:1]),
                                    ('after-close', '2026-10-30T23:59:40Z', market['bars']['SPY'])]:
        run = work / label
        run.mkdir()
        with patch.object(m, 'request', return_value={'bars': {'SPY': rows}}):
            result = m.market_data(run, {'SPY'}, {'end': cutoff})
        assert result['bars']['SPY'] == expected
        # Resume applies the same invariant without fetching or reading credentials.
        m.save(run / 'market.json', {**result, 'bars': {'SPY': rows}})
        with patch.object(m, 'secret', side_effect=AssertionError('No credential access on resume')), \
             patch.object(m, 'request', side_effect=AssertionError('No paid request on resume')):
            assert m.market_data(run, {'SPY'}, {'end': cutoff})['bars']['SPY'] == expected
            assert m.read(run / 'market.json')['bars']['SPY'] == expected
        assert m.compact_market(result)['symbols']['SPY']['latest'] == expected[-1]
    for cached in (False, True):
        run = work / ('empty-cached' if cached else 'empty-fresh')
        run.mkdir()
        invalid = {'SPY': rows[-1:]}
        if cached: m.save(run / 'market.json', {**market, 'bars': invalid})
        with patch.object(m, 'request', return_value={'bars': invalid}):
            try: m.market_data(run, {'SPY'}, {'end': '2026-10-30T23:59:40Z'})
            except RuntimeError as exc: assert 'completed SPY' in str(exc)
            else: raise AssertionError('Rejected rows satisfied SPY requirement')
        if not cached: assert not (run / 'market.json').exists()
print('PASS: fetched/resumed market windows exclude stale, provisional and future rows while requiring completed SPY evidence')

with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp)
    run = work / 'runs/demo'
    claims = [c for c in final['claims'] if c['paragraph'] == 3]
    assert [c['kind'] for c in claims] == ['observed', 'inference']
    endpoints = [{'symbol': 'SPY', 'timestamp': row['t']} for row in market['bars']['SPY']]
    assert all(c['market_rows'] == endpoints for c in claims)
    assert claims[1]['post_ids'] == ['1', '2'] and 'no breadth series' in claims[1]['explanation']
    for name, value in [('posts.json', bundle), ('market.json', market), ('final.json', final),
                        ('manifest.json', {'start': bundle['start'], 'end': bundle['end']})]:
        m.save(run / name, value)
    pdf = work / 'synthetic.pdf'
    pdf.write_bytes(b'synthetic archive fixture')
    record = m.provenance.archive(run, pdf, work)
    assert m.provenance.verify(record)
    for row in endpoints:
        anchor = m.provenance.row_id(row['symbol'], row['timestamp'])
        assert 'market.html#' + anchor in (record / 'index.html').read_text()
        assert 'id="' + anchor + '"' in (record / 'market.html').read_text()
    assert m.read(record / 'source/final.json')['paragraphs'] == final['paragraphs']
print('PASS: demo separates observed price endpoints from interpretation and archives both linked market rows')
