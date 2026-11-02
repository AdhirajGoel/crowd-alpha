"""Run isolated, offline regression scripts without user configuration."""
from pathlib import Path
import os
import subprocess
import sys
root=Path(__file__).resolve().parents[1]
env=dict(os.environ,PYTHONPATH=str(root/'src'))
for path in sorted((root/'tests').glob('test_*.py')):
    harness="import market_brief as m, runpy; m.ACCOUNTS={str(1000000000000000000+i):'demo_source_'+str(i) for i in range(1,9)}; runpy.run_path("+repr(str(path))+")"
    subprocess.run([sys.executable,'-c',harness],env=env,check=True,cwd=root)
print('All offline checks passed.')
