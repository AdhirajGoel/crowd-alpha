"""Explicit setup steps. No scheduled or paid work occurs on import."""
import argparse
from datetime import datetime
import fcntl
import getpass
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo
import engine
import market_brief as m
import settings


def main():
    p=argparse.ArgumentParser(description=__doc__)
    group=p.add_mutually_exclusive_group(required=True)
    for flag in ('setup','credentials','resolve-accounts','check','schedule','unschedule','demo','smoke-engine'):
        group.add_argument('--'+flag,action='store_true')
    p.add_argument('--engine',choices=['codex','claude'])
    p.add_argument('--model')
    p.add_argument('--accounts',nargs='+')
    p.add_argument('--x-monthly-usd',type=float)
    p.add_argument('--x-resource-usd',type=float)
    p.add_argument('--timezone',default='America/Los_Angeles')
    p.add_argument('--hour',type=int,default=17)
    args=p.parse_args()
    if args.demo:
        import demo
        demo.run();return
    if args.setup:
        if not args.engine or not args.accounts or args.x_monthly_usd is None or args.x_resource_usd is None:
            p.error('--setup requires --engine, --accounts, --x-monthly-usd and --x-resource-usd')
        if not 0<=args.hour<=23:p.error('--hour must be 0 through 23')
        handles=[a.lstrip('@') for a in args.accounts]
        if not 1<=len(handles)<=20 or any(not re.fullmatch(r'[A-Za-z0-9_]{1,15}',h) for h in handles):
            p.error('Supply 1–20 valid X handles')
        if len({h.lower() for h in handles})!=len(handles):p.error('Duplicate handles')
        executable=shutil.which(args.engine)
        if executable is None:p.error('Install the selected CLI first; see docs/setup.md')
        config={'engine':args.engine,'executable':executable,
                'model':args.model or ('gpt-6-astra' if args.engine=='codex' else 'opus'),
                'timezone':args.timezone,'hour':args.hour,
                'installed_date':datetime.now(ZoneInfo(args.timezone)).date().isoformat(),
                'x_monthly_usd':args.x_monthly_usd,'x_resource_usd':args.x_resource_usd,
                'pending_handles':handles,'accounts':{}}
        settings.validate(dict(config,accounts={'1':handles[0]}))
        m.WORK.mkdir(parents=True,exist_ok=True)
        path=m.WORK/'config.json'
        # Never overwrite configuration or reset a spending ledger during setup.
        with path.open('x') as out:json.dump(config,out,indent=2);out.write('\n')
        print('Local configuration created. Next: --credentials, then --resolve-accounts (paid X lookup).')
        return
    if args.credentials:
        if not sys.stdin.isatty():p.error('Run --credentials yourself in a terminal; never paste secrets into agent chat')
        for key in ('x','key','secret'):
            value=getpass.getpass({'x':'X bearer token: ','key':'Alpaca key ID: ','secret':'Alpaca secret: '}[key])
            if not value.strip():p.error('Empty credential; existing entries were not removed')
            # Pipe to security's command interpreter so secrets are absent from process arguments.
            import shlex
            command='add-generic-password -U -a '+shlex.quote(getpass.getuser())+' -s '+shlex.quote('crowd-alpha.'+m.SERVICES[key])+' -w '+shlex.quote(value)+'\n'
            result=subprocess.run(['/usr/bin/security','-i'],input=command,text=True,capture_output=True)
            if result.returncode:p.error('Keychain enrollment failed; credentials were not printed')
        print('Credentials enrolled in macOS Keychain.');return
    if args.resolve_accounts:
        config=m.read(m.WORK/'config.json')
        if not config:p.error('Run --setup first')
        handles=config.get('pending_handles')
        if not handles:
            print('Account roster already resolved.');return
        settings.validate(dict(config,accounts={'1':handles[0]}))
        with (m.WORK/'run.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            target=m.WORK/'setup'/'accounts.json';target.parent.mkdir(parents=True,exist_ok=True)
            data=m.XReader(config).get('/users/by',{'usernames':','.join(handles)},len(handles),target)
            roster={a['id']:a['username'] for a in data.get('data',[])}
            if data.get('errors') or {h.lower() for h in roster.values()}!={h.lower() for h in handles}:
                raise RuntimeError('Not every requested handle resolved; review the private setup response')
            config.update(accounts=roster);config.pop('pending_handles')
            settings.validate(config);m.save(m.WORK/'config.json',config)
        print('Account roster resolved.');return
    if args.unschedule:
        path=Path.home()/'Library/LaunchAgents/org.crowd-alpha.daily.plist'
        if path.exists():
            result=subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}',str(path)],capture_output=True)
            if result.returncode:
                raise RuntimeError('Could not stop the schedule; its plist was preserved. Resolve the launchctl failure and retry --unschedule.')
            path.unlink()
        print('Crowd Alpha schedule removed.');return
    config=m.initialize()
    config,version=engine.resolve(config)
    if args.check:
        import PIL,reportlab,pypdf,jsonschema
        for key in ('x','key','secret'):m.secret(key)
        print(f'Ready: {config["engine"]} ({version}), {len(config["accounts"])} accounts; local checks only.')
        return
    if args.smoke_engine:
        import tempfile
        with tempfile.TemporaryDirectory(prefix='crowd-alpha-smoke-') as tmp:
            result=engine.generate(Path(tmp),'smoke','Return {"ok": true}.',{},m.obj({'ok':{'type':'boolean'}}),config)
            if result!={'ok':True}:raise RuntimeError('Engine smoke check failed')
        print('Engine authentication and structured generation passed.');return
    if args.schedule:
        # This action enables paid collection. It must be requested explicitly.
        path=Path.home()/'Library/LaunchAgents/org.crowd-alpha.daily.plist'
        if path.exists():raise RuntimeError('Schedule already exists; use --unschedule before changing it')
        entry=m.CODE.parent/'crowd_alpha.py'
        log=m.WORK/'schedule.log'
        content={'Label':'org.crowd-alpha.daily','ProgramArguments':[sys.executable,str(entry),'--scheduled'],
                 'StartInterval':1800,'RunAtLoad':True,'WorkingDirectory':str(entry.parent),
                 'StandardOutPath':str(log),'StandardErrorPath':str(log),
                 'EnvironmentVariables':{'CROWD_ALPHA_HOME':str(m.WORK)},'Umask':63}
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as out:plistlib.dump(content,out)
        try:subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True)
        except Exception:
            path.unlink();raise
        print('Schedule enabled; the Mac must be awake and logged in.');return


if __name__=='__main__':main()
