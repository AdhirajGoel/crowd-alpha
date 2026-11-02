"""Replay synthetic inputs through the report, evidence archive, and memory pipeline."""
import json
import shlex
import sys
from pathlib import Path
import tempfile
from unittest.mock import patch
from zoneinfo import ZoneInfo
import market_brief as m


def fixtures():
    stamp='2026-10-30T20:00:00Z'
    posts=[{'id':str(i),'author':author,'author_id':str(i),'created_at':stamp,'text':text,'references':[],'media_keys':[]}
           for i,author,text in [(1,'demo_source_1','Synthetic example: SPY has recovered its recent range. I want broader participation before adding exposure.'),
                                  (2,'demo_source_2','Synthetic example: the index rebound looks fragile while smaller companies lag. A close below the range would weaken the case.')]]
    bundle={'start':'2026-10-30T00:59:40Z','end':'2026-10-31T00:59:40Z','posts':posts,'context':[],
            'coverage':{'demo_source_1':1,'demo_source_2':1},'unavailable_context_ids':[]}
    market={'feed':'synthetic','adjustment':'synthetic','as_of':bundle['end'],'missing_symbols':[],
            'limitations':'Demonstration data only; these are invented prices.',
            'bars':{'SPY':[{'t':'2026-10-29T04:00:00Z','c':100.0},{'t':'2026-10-30T04:00:00Z','c':101.0}]}}
    paragraphs=[
      '<b>Synthetic demonstration.</b> This sample uses fictional posts and prices to show how Crowd Alpha connects competing views. It contains no live market assessment.',
      '<b>Outlook:</b> The two sources agree that the rebound deserves attention, but disagree about its strength. The useful question is whether participation improves enough to support the index move.',
      '<b>Today:</b> In the supplied example, SPY rises from 100 to 101. That supports the observation that prices recovered, while leaving the broader participation question open.',
      '<b>Near term:</b> Watch whether the recovered range holds over the next few sessions. A loss of that range would weaken the bullish case; improving participation would strengthen it. The sources have not supplied a precise entry level.',
      '<b>Medium term:</b> Retain both interpretations in memory and reassess when new evidence arrives. Repetition of the same thesis will not count as independent confirmation.',
      '<b>Bonds, inflation & Fed:</b> This offline example includes no macro releases. A live brief uses dated Treasury, BLS, BEA and Federal Reserve sources to connect rates and inflation with the market view.']
    claims=[{'paragraph':i,'claim':p.split('</b>')[-1].strip(),'kind':'inference',
             'post_ids':(['1','2'] if i in (2,4,5) else []),'web_source_ids':[],
             'market_rows':([{'symbol':'SPY','timestamp':'2026-10-30T04:00:00Z'}] if i==3 else []),
             'explanation':'Synthetic example; this mapping points to the fixture inputs used by the paragraph.'}
             for i,p in enumerate(paragraphs,1)]
    result={'paragraphs':paragraphs,'evidence_ids':['1','2'],'claims':claims,
            'account_memory':[{'account':p['author'],'note':'Synthetic source; no performance claim.'} for p in posts],
            'new_calls':[], 'market_view':{'horizons':[{'horizon':h,'thesis':'Rebound needs broader confirmation.',
            'supporting_evidence':['1','2'],'competing_view':'Small-company weakness may undermine the rebound.',
            'would_strengthen':['Improving participation'],'would_invalidate':['Loss of recovered range'],
            'uncertainty':'No breadth series is included in this demonstration.'} for h in ('today','near_term','medium_term')],
            'macro_outlook':'No macro release included in the synthetic fixture.','stock_watchlist':[],
            'change_summary':'Initial synthetic demonstration.','unresolved_questions':['Does participation improve?']}}
    selection={'posts':[{'id':p['id'],'relevant':True,'image_post_ids':[],'image_reason':'','symbols':['SPY']} for p in posts]}
    return bundle,market,result,selection


def run():
    folder=m.WORK/'demos';folder.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='example-',dir=folder));reports=work/'reports';reports.mkdir()
    bundle,market,result,selection=fixtures()
    config={'engine':'codex','model':'synthetic-replay','executable':'unused','installed_date':'2026-10-30'}
    m.save(work/'config.json',config)
    job=work/'runs/2026-10-30'
    for name,data in [('posts.json',bundle),('market.json',market),('brief.json',result),('triage.json',selection),
                      ('manifest.json',{'due_day':'2026-10-30','start':bundle['start'],'end':bundle['end']}),
                      ('macro.json',{'as_of':bundle['end'],'fingerprints':{},'sources':[],'errors':[],'limitations':'Synthetic demo: no macro retrieval.'})]:m.save(job/name,data)
    with patch.object(m,'WORK',work),patch.object(m,'ROOT',reports),patch.object(m,'PT',ZoneInfo('America/Los_Angeles')), \
         patch.object(m,'ACCOUNTS',{'1':'demo_source_1','2':'demo_source_2'}), \
         patch.object(m,'now',return_value=m.parse('2026-10-31T01:00:00Z')), \
         patch.object(m,'resolve_codex',return_value=(config,'synthetic replay')), \
         patch.object(m.macro_data,'collect',return_value=m.read(job/'macro.json')):
        m.run_job(day='2026-10-30')
        record=work/'provenance/2026-10-30'
        count=m.provenance.verify(record)
        assert m.read(work/'live/current.json')['view']['unresolved_questions']==['Does participation improve?']
        m.live_memory.remember(work,{'kind':'question','author':'user','text':'Does participation improve?',
                                  'evidence_files':[str(record/'source/posts.json')]})
        assert len(m.live_memory.context(work)['discussion_notes'])==1
    print(f'Synthetic replay passed: PDF, {count} archived files, market view and discussion memory.')
    print(f'Demo state: {work}')
    print(f'Evidence index: {record / "index.html"}')
    print('Read demo memory: ' + shlex.join(['env', 'CROWD_ALPHA_HOME='+str(work), sys.executable, str(m.CODE/'live_memory.py')]))
    return reports/'2026-10-30-market-brief.pdf'
