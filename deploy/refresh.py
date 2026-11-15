"""Scheduled synchronization retries only failed sources once."""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector import refresh

def run_sync(refresh_fn=refresh, sleeper=time.sleep):
    result = refresh_fn()
    if result is None: return 0
    failed = [s['id'] for s in result['sources'] if s['status'] == 'error']
    if failed:
        print('Retrying failed sources:', ', '.join(failed), flush=True)
        sleeper(5)
        result = refresh_fn(failed) or result
    return int(bool(result.get('detailFailures')) or any(s['status'] != 'ok' for s in result['sources']))

if __name__ == '__main__':
    raise SystemExit(run_sync())
