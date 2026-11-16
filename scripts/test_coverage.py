"""Run full Python tests and retain diagnostic coverage even on test failure."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent


def run(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    env={**os.environ,'COVERAGE_FILE':str(output/'.coverage')}
    coverage=[sys.executable,'-m','coverage']
    def call(args):return subprocess.run(args,cwd=ROOT,env=env,check=False).returncode
    # Erase only this command's data file; never merge another checkout's history.
    if call([*coverage,'erase']):return 2
    tests=call([*coverage,'run','--rcfile=backend/tests/project.coveragerc','-m','pytest','backend/tests','-q','--tb=short','--junitxml='+str(output/'tests.xml')])
    (output/'test-exit.json').write_text(json.dumps({'pytest_exit_code':tests})+'\n')
    codes=[call([*coverage,'json','--rcfile=backend/tests/project.coveragerc','-o',str(output/'coverage.json')]),
           call([*coverage,'xml','--rcfile=backend/tests/project.coveragerc','-o',str(output/'coverage.xml')]),
           call([*coverage,'html','--rcfile=backend/tests/project.coveragerc','-d',str(output/'html')])]
    if not codes[0]:
        codes.append(call([sys.executable,'-m','scripts.coverage_report','--input',str(output/'coverage.json'),'--output',str(output)]))
    return tests or next((code for code in codes if code),0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='coverage-artifacts/python')
    args=parser.parse_args()
    return run(args.output)


if __name__=='__main__':raise SystemExit(main())
