"""Transient price/image failures recover without discarding cached evidence."""
import io
from http.client import RemoteDisconnected
import tempfile
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import market_brief as m

image = io.BytesIO()
Image.new('RGB', (2, 2), 'white').save(image, format='PNG')
class Response:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def geturl(self): return 'https://pbs.twimg.com/media/test'
    def read(self, *args): return image.getvalue()
with tempfile.TemporaryDirectory() as tmp:
    run = Path(tmp)
    m.save(run / 'image-lookup-0000.json', {'data': [{'id': '1', 'attachments': {'media_keys': ['photo']}}],
        'includes': {'media': [{'media_key': 'photo', 'type': 'photo', 'url': 'https://pbs.twimg.com/media/test'}]}})
    with patch.object(m, 'secret', return_value='test'), patch.object(m.time, 'sleep'), \
         patch.object(m.urllib.request, 'urlopen', side_effect=[m.urllib.error.URLError('timeout'),
             RemoteDisconnected('Remote end closed connection without response'), Response()]) as fetch:
        result = m.images_for(run, {'posts': [{'image_post_ids': ['1']}]}, {})
        assert len(result['photos']) == 1 and fetch.call_count == 3
    with patch.object(m, 'secret', return_value='test'), patch.object(m.time, 'sleep'), \
         patch.object(m, 'request', side_effect=[TimeoutError('timeout'), RemoteDisconnected(
             'Remote end closed connection without response'), {'bars': {'SPY': [
             {'t': '2026-09-28T04:00:00Z', 'c': 100}]}}]) as fetch:
        market = m.market_data(run, {'SPY'}, {'end': '2026-09-28T23:59:45Z'})
        assert market['feed'] == 'sip' and fetch.call_count == 3
    m.save(run / 'x-page-000.json', {'errors': [{'detail': 'temporary partial search'}]})
    with patch.object(m, 'secret', return_value='test'), \
         patch.object(m.XReader, 'get', return_value={'data': [], 'meta': {}}) as fetch:
        try: m.collect(run, {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-02T00:00:00Z'}, {})
        except RuntimeError as exc: assert 'partial search' in str(exc)
        else: raise AssertionError('Partial search accepted')
        assert not (run / 'x-page-000.json').exists()
        assert len(list((run / 'rejected-responses').glob('*/x-page-000.json'))) == 1
        assert not m.collect(run, {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-02T00:00:00Z'}, {})['posts']
        assert fetch.call_count == 1
with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'WORK', Path(tmp)), \
     patch.object(m, 'secret', return_value='test'), patch.object(m.time, 'sleep'):
    config = {'x_resource_usd': 0.005, 'x_monthly_usd': 30}
    reader = m.XReader(config)
    ledger = Path(tmp) / 'x-budget.json'
    month = m.now().astimezone(m.PT).strftime('%Y-%m')
    disconnected = RemoteDisconnected('Remote end closed connection without response')
    with patch.object(m, 'request', side_effect=[disconnected, {'data': [{'id': '1'}]}]) as fetch:
        assert reader.get('/tweets', {}, 100, Path(tmp) / 'response.json')['data']
        assert fetch.call_count == 2
        assert m.read(ledger)[month] == 0.505  # Uncertain attempt keeps its reservation.
    m.save(ledger, {})
    with patch.object(m, 'request', side_effect=disconnected) as fetch:
        try: reader.get('/tweets', {}, 100, Path(tmp) / 'response.json')
        except RuntimeError as exc: assert 'reservation retained' in str(exc)
        else: raise AssertionError('Disconnect retry limit ignored')
        assert fetch.call_count == 4 and m.read(ledger)[month] == 2.0
    m.save(ledger, {month: 29.5})
    with patch.object(m, 'request', side_effect=disconnected) as fetch:
        try: reader.get('/tweets', {}, 100, Path(tmp) / 'response.json')
        except RuntimeError as exc: assert 'budget would exceed' in str(exc)
        else: raise AssertionError('Retry exceeded budget')
        assert fetch.call_count == 1 and m.read(ledger)[month] == 30.0
print('PASS: transient X, image and Alpaca retries preserve evidence and spending limits')

with tempfile.TemporaryDirectory() as tmp, patch.object(m, 'secret', return_value='synthetic'), \
     patch.object(m, 'now', return_value=m.parse('2026-10-30T17:00:00Z')), \
     patch.object(m, 'request', return_value={'bars':{'SPY':[{'t':'2026-10-30T04:00:00Z','c':100}]}}):
    try:m.market_data(Path(tmp), {'SPY'}, {'end':'2026-10-30T17:00:00Z'})
    except RuntimeError as error:assert 'completed SPY' in str(error)
    else:raise AssertionError('Empty completed-session market accepted')
    assert not (Path(tmp) / 'market.json').exists()
print('PASS: provisional SPY bars cannot satisfy completed-session requirement')
