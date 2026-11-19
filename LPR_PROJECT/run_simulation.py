#!/usr/bin/env python3
"""Run fresh native integrated simulation and publish its actual decisions."""
from pathlib import Path
import os,sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'code'))
from close_integration import main
if __name__=='__main__':
    import webbrowser
    no_open='--no-open' in sys.argv
    if no_open:sys.argv.remove('--no-open')
    batch=main()
    if batch is not None:
        report=batch/'index.html'
        print('Decision report:',report.resolve())
        if not no_open:
            try:webbrowser.open(report.resolve().as_uri())
            except Exception:pass
