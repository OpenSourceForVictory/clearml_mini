"""Bound execution time even when the worker process is unavailable."""
import argparse
import os
import signal
import subprocess
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--timeout', type=int, required=True)
    p.add_argument('command', nargs=argparse.REMAINDER)
    args = p.parse_args()
    child = subprocess.Popen(args.command, start_new_session=True)
    try:
        code = child.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        print('execution deadline exceeded', file=sys.stderr, flush=True)
        code = 124
    raise SystemExit(code)


if __name__ == '__main__':
    main()
