#!/usr/bin/env python3
"""
Feed sample rtl_433 JSON at the plugin, for testing without a radio.

    ./tools/replay.py --host eisy.local --file tools/samples.jsonl
    ./tools/replay.py --host eisy.local --loop --interval 5

With --loop the sample records are re-sent continuously with jittered
values, which is the quickest way to confirm that nodes appear in the admin
console and that the freshness and offline drivers behave.

Standard library only; runs anywhere Python 3 does.
"""

import argparse
import json
import os
import random
import socket
import sys
import time

DEFAULT_SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'samples.jsonl')


def load_samples(path):
    records = []
    with open(path, 'r') as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            records.append(json.loads(line))
    return records


def jitter(record):
    """Nudge the numeric readings so each pass looks like a fresh packet."""
    out = dict(record)
    for key in ('temperature_C', 'temperature_F', 'current', 'pressure_kPa'):
        if key in out and isinstance(out[key], (int, float)):
            out[key] = round(out[key] * random.uniform(0.97, 1.03), 3)
    for key in ('Humidity', 'humidity'):
        if key in out and isinstance(out[key], (int, float)):
            out[key] = max(0, min(100, out[key] + random.randint(-2, 2)))
    out['time'] = time.strftime('%Y-%m-%d %H:%M:%S')
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1',
                        help='host running the plugin (default 127.0.0.1)')
    parser.add_argument('--port', type=int, default=1433,
                        help='plugin listen port (default 1433)')
    parser.add_argument('--file', default=DEFAULT_SAMPLES,
                        help='newline-delimited JSON to send')
    parser.add_argument('--loop', action='store_true',
                        help='keep sending until interrupted')
    parser.add_argument('--interval', type=float, default=5.0,
                        help='seconds between passes when looping')
    args = parser.parse_args()

    records = load_samples(args.file)
    if not records:
        sys.exit('No records found in {}'.format(args.file))

    print('Connecting to {}:{}'.format(args.host, args.port))
    sock = socket.create_connection((args.host, args.port), timeout=10)
    sent = 0
    try:
        while True:
            for record in records:
                payload = jitter(record) if args.loop else record
                line = json.dumps(payload) + '\n'
                sock.sendall(line.encode('utf-8'))
                sent += 1
                print('sent: {}'.format(line.strip()))
            if not args.loop:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        print('Sent {} record(s)'.format(sent))


if __name__ == '__main__':
    main()
