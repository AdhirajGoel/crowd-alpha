#!/usr/bin/env python3
"""Crowd Alpha command entry point."""
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
os.umask(0o077)
if __name__=='__main__':
    if any(flag in sys.argv for flag in ('--setup','--credentials','--resolve-accounts','--check','--schedule','--unschedule','--demo','--smoke-engine')):
        import onboarding
        onboarding.main()
    else:
        import market_brief
        market_brief.main()
