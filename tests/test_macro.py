"""Offline checks for official macro extraction and provenance boundaries."""
import json
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
import macro_data as m
import provenance as p

rows = m.treasury_rows('Date,2 Yr,5 Yr,10 Yr\n09/25/2026,5,5,5\n09/24/2026,4.87,5.03,5.18\n09/23/2026,N/A,4,5\n', date(2026,9,24))
assert rows == [{'date':'2026-09-24','2y':4.87,'5y':5.03,'10y':5.18}]
series = [{'seriesID':s,'data':[{'year':'2026','period':'M08','value':'103.4'}, {'year':'2025','period':'M08','value':'100'}, {'year':'2026','period':'M13','value':'999'}]} for s in ('CUUR0000SA0','CUUR0000SA0L1E')]
assert all(s['year_over_year_pct']==3.4 for s in m.cpi_summary({'status':'REQUEST_SUCCEEDED','Results':{'series':series}},date(2026,9,24)).values())
assert m.plain_text('<!--bad--><script>bad</script><p>A &amp; B</p>') == 'A & B'
with tempfile.TemporaryDirectory() as tmp, patch.object(m.urllib.request,'urlopen',side_effect=OSError('offline')):
    result=m.collect(Path(tmp),datetime(2026,9,24,tzinfo=timezone.utc))
    assert not result['sources'] and len(result['errors'])==4
    assert len(list((Path(tmp)/'macro').glob('*.request.json')))==4
    assert m.collect(Path(tmp),datetime.now(timezone.utc)) == result
claim={'paragraph':1,'claim':'Yield is 4.87%','kind':'observed','post_ids':[], 'market_rows':[], 'web_source_ids':['treasury'],'explanation':'Official observation.'}
result={'paragraphs':['Yield is 4.87%'],'claims':[claim]}
p.validate_claims(result,{'posts':[],'context':[]},{'bars':{}},{'sources':[{'id':'treasury'}]})
try:
    p.validate_claims(result,{'posts':[],'context':[]},{'bars':{}},{'sources':[]})
except RuntimeError: pass
else: raise AssertionError('Unknown web evidence accepted')
for s in series:
    for x in s['data']: x['year']=str(int(x['year'])-1)
assert all(s['year_over_year_pct']==3.4 for s in m.cpi_summary({'status':'REQUEST_SUCCEEDED','Results':{'series':series}},date(2026,1,1)).values())
print('PASS: yield cutoff/missing values, CPI calculation, HTML extraction, partial failure/cache, web provenance validation')
import market_brief as brief
sample={'paragraphs':['<b>Bonds, inflation & Fed:</b> Official yields.'], 'evidence_ids':['treasury'], 'account_memory':[{'account':a,'note':''} for a in brief.ACCOUNTS.values()], 'new_calls':[]}
brief.verify_brief(sample,{'posts':[],'context':[]},macro_required=True,macro={'sources':[{'id':'treasury'}]})
try: brief.verify_brief(sample,{'posts':[],'context':[]},macro_required=True,macro={'sources':[]})
except RuntimeError: pass
else: raise AssertionError('Unknown top-level macro evidence accepted')
print('PASS: top-level evidence accepts captured macro IDs and rejects missing sources')

# A failed fresh fetch can cite the immediately preceding supplied snapshot.
old = {'id': 'treasury', 'text': 'Dated prior yields', 'retrieved_at': '2026-10-05T23:00:00Z'}
fallback = {'sources': [], 'previous_record': {'sources': [old]}}
brief.verify_brief(sample, {'posts': [], 'context': []}, True, fallback)
p.validate_claims({'paragraphs': ['Prior yields'], 'claims': [claim]},
                  {'posts': [], 'context': []}, {'bars': {}}, fallback)
assert p.macro_sources(fallback) == [dict(old, previous_report_snapshot=True)]
fresh = dict(old, text='Fresh yields')
assert p.macro_sources(dict(fallback, sources=[fresh])) == [fresh]
assert 'previous_report_snapshot' not in old
assert p.macro_sources({'previous_record': {'previous_record': fallback['previous_record']}}) == []
print('PASS: prior macro fallback, current-source precedence, immutable inputs, no recursive stale citations')

# A historical report must not use an inflation release published after its cutoff.
class HistoricalResponse:
    status = 200
    headers = {'Content-Type': 'text/html'}
    def __init__(self, url): self.url = url
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def geturl(self): return self.url
    def read(self, *args):
        if '/data/' in self.url:
            return b'<a href="/news/2026/personal-income-and-outlays-august-2026">Release</a>'
        return b'<p>EMBARGOED UNTIL RELEASE AT 8:30 a.m. EDT, Wednesday, September 30, 2026 PCE price index NEW</p>'
def historical_fetch(request, **kwargs):
    if request.full_url.startswith('https://www.bea.gov/'):
        return HistoricalResponse(request.full_url)
    raise OSError('No network in test')
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    source = root / 'prior'
    (source / 'macro').mkdir(parents=True)
    items = []
    for identifier in ('cpi', 'pce'):
        item = {'id': identifier, 'title': identifier, 'text': 'OLD', 'summary': None,
                'retrieved_at': '2026-09-27T23:59:00Z', 'url': 'https://www.bea.gov/',
                'raw_file': f'macro/{identifier}.raw.txt', 'text_file': f'macro/{identifier}.txt',
                'fingerprint': 'old'}
        items.append(item)
        for key in ('raw_file', 'text_file'): (source / item[key]).write_text('OLD')
    m.save(source / 'macro.json', {'sources': items})
    with patch.object(m.urllib.request, 'urlopen', side_effect=historical_fetch):
        for day in (29, 30):
            run = root / str(day); run.mkdir()
            result = m.collect(run, datetime(2026, 9, day, 23, tzinfo=timezone.utc),
                               historical=True, historical_source=source)
            by_id = {s['id']: s for s in result['sources']}
            assert by_id['cpi']['text'] == 'OLD'
            assert by_id['pce']['text'] == 'OLD' if day == 29 else 'NEW' in by_id['pce']['text']
            assert (run / by_id['pce']['raw_file']).is_file()
print('PASS: pre-cutoff CPI snapshots, future PCE exclusion, release-day PCE inclusion, retained source files')

# Keep only the immediately preceding snapshot; archived history stays on disk.
with tempfile.TemporaryDirectory() as tmp, patch.object(m.urllib.request, 'urlopen', side_effect=OSError('offline')):
    previous = {'sources':[old], 'fingerprints':{}, 'previous_record':{'sources':[fresh]}}
    for day in range(3):
        result = m.collect(Path(tmp) / str(day), datetime(2026, 10, day + 1, tzinfo=timezone.utc), previous)
        assert result['previous_record'] == {k:v for k,v in previous.items() if k != 'previous_record'}
        assert 'previous_record' not in result['previous_record']
        previous = result
print('PASS: bounded macro context retains one previous snapshot')
