"""Explicit, interruptible feed polling. No daemon is enabled by installation."""
import argparse
import json
import signal
import sys
from datetime import date, datetime
from threading import Event
from zoneinfo import ZoneInfo

from .providers import FeedError, SyncRequest, sync


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider',choices=['nba','sportsdataio'],default='nba')
    parser.add_argument('--feed',default='scoreboard')
    parser.add_argument('--game-id')
    parser.add_argument('--date',type=date.fromisoformat,help='YYYY-MM-DD. Daily SportsDataIO feeds default to the current New York date.')
    parser.add_argument('--watch',action='store_true',help='Poll until interrupted; archives unchanged observations too')
    parser.add_argument('--interval',type=int,default=60,help='Seconds between polls; minimum 30. Check your subscription quota.')
    args = parser.parse_args()
    if args.interval < 30:
        parser.error('Interval must be at least 30 seconds')
    stop = Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_:stop.set())
    failures = 0
    while not stop.is_set():
        day = args.date
        if args.provider == 'sportsdataio' and args.feed in {'games','player_stats','projections'} and day is None:
            day = datetime.now(ZoneInfo('America/New_York')).date()
        try:
            request = SyncRequest(provider=args.provider,feed=args.feed,game_id=args.game_id,date=day)
        except ValueError as exc:
            parser.error(str(exc))
        delay = args.interval
        try:
            result = sync(request)
            print(json.dumps({'snapshot_id':result['snapshot']['id'],'records':result['record_count'],
                              'received_at':result['snapshot']['received_at']}),flush=True)
            failures = 0
        except FeedError as exc:
            failures += 1
            print(json.dumps({'error':str(exc),'snapshot_id':exc.snapshot_id}),file=sys.stderr,flush=True)
            if not args.watch:
                return 2
            delay = max(args.interval,min(1800,args.interval*2**min(failures,8)),exc.retry_after or 0)
        if not args.watch:
            return 0
        stop.wait(delay)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
