"""Retrieve fresh posts for a market discussion without publishing a daily brief."""
import argparse
import fcntl
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
import market_brief as m


def query(hours=6, handles=None):
    if not 0 < hours <= 167:
        raise ValueError("Hours must be greater than 0 and at most 167 (within recent-search retention)")
    requested = {h.lstrip('@').lower() for h in handles} if handles else {h.lower() for h in m.ACCOUNTS.values()}
    accounts = {i: h for i, h in m.ACCOUNTS.items() if h.lower() in requested}
    if requested - {h.lower() for h in accounts.values()}:
        raise ValueError("Choose accounts from the configured roster: " + ', '.join(m.ACCOUNTS.values()))
    # Share the daily job's lock so both collectors safely use the same budget ledger.
    with (m.WORK / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another collection is running; retry when it finishes") from None
        end = m.now() - timedelta(seconds=20)
        folder = m.WORK / 'live' / 'queries'
        folder.mkdir(parents=True, exist_ok=True)
        run = Path(tempfile.mkdtemp(prefix=end.strftime('%Y%m%dT%H%M%SZ-'), dir=folder))
        manifest = {'start': m.stamp(end - timedelta(hours=hours)), 'end': m.stamp(end),
                    'requested_at': m.stamp(m.now()), 'accounts': accounts,
                    'hours': hours, 'state': 'collecting'}
        m.save(run / 'manifest.json', manifest)
        try:
            bundle = m.collect(run, manifest, m.read(m.WORK / 'config.json'), accounts=accounts)
        except Exception as exc:
            m.save(run / 'manifest.json', {**manifest, 'state': 'failed',
                   'failed_at': m.stamp(m.now()), 'error': str(exc)})
            print(f"Incomplete lookup evidence: {run}", file=sys.stderr)
            raise
        m.save(run / 'manifest.json', {**manifest, 'state': 'complete',
               'completed_at': m.stamp(m.now()), 'coverage': bundle['coverage'],
               'unavailable_context_ids': bundle['unavailable_context_ids']})
        return run


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours', type=float, default=6, help='Lookback hours, default 6; maximum 167')
    parser.add_argument('--accounts', nargs='+', help='Optional subset of configured handles, with or without @')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        m.initialize()
        run = query(args.hours, args.accounts)
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"X lookup failed: {exc}\n")
    print(f"Read posts and context: {run / 'posts.json'}")
    print(f"Window, retrieval time and coverage: {run / 'manifest.json'}")
