import sys, tempfile, json
from pathlib import Path
from unittest.mock import patch
import market_brief as m
import provenance as p
class Response:
    status=200
    headers={'Date':'test-date', 'Authorization':'do-not-record'}
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self): return b'{"bars": {"SPY": []}, "next_page_token": null}'
with tempfile.TemporaryDirectory() as tmp:
    work=Path(tmp)/'.work'
    run=work/'runs/2026-09-24'
    run.mkdir(parents=True)
    with patch.object(m.urllib.request, 'urlopen', return_value=Response()):
        m.request('https://data.alpaca.markets/v2/stocks/bars', {'Authorization':'SECRET'}, {'symbols':'SPY'}, run/'alpaca-page-000.json')
    meta=m.read(run/'alpaca-page-000.request.json')
    assert meta['parameters']=={'symbols':'SPY'} and meta['response_sha256']==p.digest(run/'alpaca-page-000.json')
    assert 'SECRET' not in json.dumps(meta) and 'Authorization' not in json.dumps(meta)
    bundle={'posts':[{'id':'1','text':'<script>do bad things</script>', 'author':'tester', 'created_at':'2026-09-24T00:00:00Z', 'references':[]}], 'context':[]}
    market={'feed':'sip','adjustment':'all','as_of':'2026-09-25T00:00:00Z','limitations':'daily bars', 'bars':{'SPY':[{'t':'2026-09-24T04:00:00Z','c':100}]}}
    claim={'paragraph':1,'claim':'SPY closed at 100','kind':'observed','post_ids':['1'], 'market_rows':[{'symbol':'SPY','timestamp':'2026-09-24T04:00:00Z'}], 'explanation':'Daily close.'}
    result={'paragraphs':['SPY closed at 100'], 'claims':[claim]}
    for name, data in [('posts.json',bundle),('market.json',market),('final.json',result),('manifest.json',{'start':'2026-09-24T00:00:00Z','end':'2026-09-25T00:00:00Z'})]:
        m.save(run/name,data)
    m.save(run/'memory-after.json', {'account_notes': [], 'calls': []})
    claim['web_source_ids'] = ['treasury']
    m.save(run/'final.json', result)
    m.save(run/'macro.json', {'as_of': market['as_of'], 'fingerprints': {},
        'sources': [], 'errors': [{'source': 'treasury', 'error': 'timeout'}],
        'limitations': 'Current yields unavailable.', 'previous_record': {'sources': [{
            'id': 'treasury', 'title': 'Prior Treasury snapshot', 'url': 'https://home.treasury.gov/',
            'retrieved_at': '2026-09-23T23:00:00Z', 'text': 'Prior dated yields',
            'raw_file': 'macro/treasury.raw.txt', 'text_file': 'macro/treasury.txt'}]}})
    pdf=Path(tmp)/'2026-09-24-market-brief.pdf'
    pdf.write_bytes(b'pdf-test-fixture')
    record=p.archive(run,pdf,work)
    count=p.verify(record)
    assert p.archive(run,pdf,work)==record
    with patch.object(m, 'WORK', work), patch.object(m, 'ROOT', Path(tmp)), patch.object(m, 'now', return_value=m.parse('2026-09-25T02:00:00Z')):
        m.run_job()
        assert m.read(work/'status.json')['state']=='complete'
        assert m.read(work/'memory.json')=={'account_notes': [], 'calls': []}
        assert p.digest(pdf)==p.digest(record/pdf.name)
        m.save(work/'status.json', {'last_success_due_day':'2026-10-03','state':'complete'})
        m.save(work/'memory.json', {'newer_memory':True})
        m.publish_record(record,'2026-09-24')
        assert m.read(work/'memory.json')=={'newer_memory':True}
        assert m.read(work/'status.json')['last_success_due_day']=='2026-10-03'
        m.save(work/'status.json', {'last_success_due_day':'2026-09-24','state':'complete'})
        m.save(work/'live/current.json', {'as_of':'2026-09-25T12:00:00Z','view':{'refreshed':True}})
        pdf.write_bytes(b'corrupted')
        m.publish_record(record,'2026-09-24')
        assert p.digest(pdf)==p.digest(record/pdf.name)
        assert m.read(work/'memory.json')=={'newer_memory':True}
        assert m.read(work/'live/current.json')['view']=={'refreshed':True}
    assert '&lt;script&gt;' in (record/'posts.html').read_text()
    assert '<script>' not in (record/'posts.html').read_text()
    assert 'market.html#'+p.row_id('SPY','2026-09-24T04:00:00Z') in (record/'index.html').read_text()
    assert 'SPY,2026-09-24T04:00:00Z' in (record/'market.csv').read_text()
    assert 'macro.html#treasury' in (record/'index.html').read_text()
    macro_html = (record/'macro.html').read_text()
    assert 'id="treasury"' in macro_html and 'Previous report snapshot' in macro_html
    assert 'Prior dated yields' in macro_html and '2026-09-23T23:00:00Z' in macro_html
    assert 'source/macro.json' in macro_html and 'href="source/macro/treasury' not in macro_html
    claim['market_rows'][0]['timestamp']='invented'
    try: p.validate_claims(result,bundle,market,m.read(run/'macro.json'))
    except RuntimeError: pass
    else: raise AssertionError('Invented row accepted')
    (record/'source/market.json').unlink()
    try: p.verify(record)
    except RuntimeError: pass
    else: raise AssertionError('Missing evidence accepted')
print(f'PASS: request metadata excludes credentials; archive round trip ({count} files); publication recovery; repeat safe; post HTML escaped; links/CSV valid; invented and missing evidence rejected')
