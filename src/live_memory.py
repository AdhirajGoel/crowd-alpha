"""Persistent market views and discussion notes; no network or email access."""
import argparse
import fcntl
import json
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')
    temp.replace(path)


def context(work):
    folder = work/'live'
    notes = [read(p) for p in sorted((folder/'conversations').glob('*.json'))]
    superseded = {i for note in notes for i in note.get('supersedes', [])}
    active = [note for note in notes if note['id'] not in superseded]
    return {'current_view': read(folder/'current.json', {}), 'discussion_notes': active,
            'policy': 'Use saved evidence first. Retrieve fresh data only when required to answer or reassess a setup. User hypotheses are not established facts; all historical views retain their original as-of time.'}


def publish(work, record):
    """Derive the current view from an immutable, verified daily evidence archive."""
    import provenance
    provenance.verify(record)
    source = record/'source'
    final = read(source/'final.json')
    state = read(source/'live-after.json')
    if state is None:
        # Bootstrap a historical brief without inventing an unrecorded earlier view.
        state = {'as_of': read(source/'manifest.json')['end'], 'record': record.name,
                 'previous_record': None, 'view': {'baseline_paragraphs': final['paragraphs'],
                 'change_summary': 'Initial baseline from the archived brief; no earlier live view is claimed.'}}
    state['provenance'] = str(record/'index.html')
    folder=work/'live';folder.mkdir(exist_ok=True)
    with (folder/'memory.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        history=folder/'history'/f'{record.name}.json'
        if history.exists() and read(history)!=state:
            raise RuntimeError('Existing market history differs; preserve it and create a revision')
        save(history,state)
        current=read(folder/'current.json',{})
        if state['as_of'] >= current.get('as_of',''):
            save(folder/'current.json',state)


def remember(work, note):
    kinds={'user_hypothesis','user_preference','watchlist','question','agent_conclusion'}
    if note.get('kind') not in kinds or not str(note.get('text','')).strip():
        raise ValueError('A discussion note needs a known kind and nonempty text')
    author=note.get('author')
    if author not in ('user','agent') or (note['kind'].startswith('user_') and author!='user'):
        raise ValueError('Keep user statements and agent conclusions distinct')
    if note['kind']=='agent_conclusion' and author!='agent':
        raise ValueError('Agent conclusions must be attributed to the agent')
    if note.get('status','open') not in ('open','resolved'):
        raise ValueError('Discussion status must be open or resolved')
    folder=work/'live';folder.mkdir(exist_ok=True)
    with (folder/'memory.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        existing={p.stem for p in (folder/'conversations').glob('*.json')}
        if set(note.get('supersedes',[]))-existing:
            raise ValueError('Cannot supersede an unknown discussion record')
        for value in note.get('evidence_files',[]):
            path=Path(value).resolve()
            if not path.is_relative_to(work.resolve()) or not path.is_file():
                raise ValueError('Evidence must be an existing file inside the brief project')
        identifier=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8]
        record={**note,'id':identifier,'recorded_at':datetime.now(timezone.utc).isoformat(),
                'status':note.get('status','open'),'supersedes':note.get('supersedes',[])}
        save(folder/'conversations'/f'{identifier}.json',record)
        return record


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remember',type=Path,help='Save a structured discussion note from a local JSON file')
    args=parser.parse_args()
    import market_brief
    work=market_brief.WORK
    print(json.dumps(remember(work,read(args.remember)) if args.remember else context(work),indent=2,ensure_ascii=False))
