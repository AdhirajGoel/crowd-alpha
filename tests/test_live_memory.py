"""Discussion preservation, source attribution, and daily view recovery."""
import json,tempfile
from pathlib import Path
import live_memory as m
import provenance
with tempfile.TemporaryDirectory() as tmp:
 w=Path(tmp)
 first=m.remember(w,{'kind':'user_hypothesis','author':'user','text':'A tentative idea.'})
 second=m.remember(w,{'kind':'agent_conclusion','author':'agent','text':'The idea remains unverified.','supersedes':[first['id']]})
 assert [n['id'] for n in m.context(w)['discussion_notes']]==[second['id']]
 assert len(list((w/'live/conversations').glob('*.json')))==2
 try:m.remember(w,{'kind':'user_hypothesis','author':'agent','text':'False attribution'})
 except ValueError:pass
 else:raise AssertionError('Misattributed note accepted')
 r=w/'provenance/2026-09-24';s=r/'source';s.mkdir(parents=True)
 for name,value in [('final.json',{'paragraphs':['A dated baseline.']}),('manifest.json',{'end':'2026-09-25T00:00:00Z'})]:
  m.save(s/name,value)
 m.save(r/'integrity.json',{'sha256':{str(p.relative_to(r)):provenance.digest(p) for p in s.iterdir()}})
 m.publish(w,r);m.publish(w,r)
 assert m.context(w)['current_view']['record']=='2026-09-24'
 assert (w/'live/history/2026-09-24.json').is_file()
 assert len(m.context(w)['discussion_notes'])==1
print('PASS: immutable discussion history, distinct authors, supersession, archive bootstrap, repeat publication preserves notes')
import market_brief as brief
assert 'market_view' in brief.BRIEF['required']
assert set(brief.LIVE_VIEW['properties'])=={'horizons','macro_outlook','stock_watchlist','change_summary','unresolved_questions'}
assert 'NO page-count limit' in brief.SYNTHESIS
print('PASS: daily synthesis schema includes persistent market view independently of brief word limit')
