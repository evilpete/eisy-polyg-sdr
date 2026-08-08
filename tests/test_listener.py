#!/usr/bin/env python3
"""
Offline tests for the TCP JSON listener.

Binds a real socket on the loopback interface and streams records at it, so
the framing, error tolerance and access control are exercised without an
EISY or a radio.
"""

import json
import logging
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rtl433 import listener       # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
LOG = logging.getLogger('test')


class ListenerHarness:
    """Starts a Listener on an ephemeral port and collects what it decodes."""

    def __init__(self, allow_from=None):
        self.records = []
        self.event = threading.Event()
        self.listener = listener.Listener(LOG)
        self.port = self._free_port()
        assert self.listener.start('127.0.0.1', self.port, self._on_record,
                                   allow_from)

    @staticmethod
    def _free_port():
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def _on_record(self, record):
        self.records.append(record)
        self.event.set()

    def send(self, payload):
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=5)
        try:
            sock.sendall(payload)
        finally:
            sock.close()

    def wait(self, count, timeout=5.0):
        deadline = time.time() + timeout
        while len(self.records) < count and time.time() < deadline:
            time.sleep(0.02)
        return self.records

    def stop(self):
        self.listener.stop()


class TestListener(unittest.TestCase):

    def setUp(self):
        self.harness = ListenerHarness()

    def tearDown(self):
        self.harness.stop()

    def test_receives_json_lines(self):
        payload = (b'{"model":"A","id":1,"temperature_C":20.0}\n'
                   b'{"model":"B","id":2,"current":3.5}\n')
        self.harness.send(payload)
        records = self.harness.wait(2)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['model'], 'A')
        self.assertEqual(records[1]['id'], 2)

    def test_ignores_blank_and_malformed_lines(self):
        payload = (b'\n'
                   b'not json at all\n'
                   b'{"model":"A","id":1}\n')
        self.harness.send(payload)
        records = self.harness.wait(1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['model'], 'A')

    def test_accepts_syslog_framed_lines(self):
        payload = b'<133>Aug  4 19:40:59 sdr rtl_433: {"model":"A","id":9}\n'
        self.harness.send(payload)
        records = self.harness.wait(1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['id'], 9)

    def test_oversize_line_is_dropped_without_killing_stream(self):
        payload = (b'{"model":"X","junk":"' + b'z' * 20000 + b'"}\n'
                   b'{"model":"A","id":1}\n')
        self.harness.send(payload)
        records = self.harness.wait(1)
        self.assertTrue(any(r.get('model') == 'A' for r in records))

    def test_multiple_concurrent_feeds(self):
        def feed(model):
            self.harness.send(
                json.dumps({'model': model, 'id': 1}).encode() + b'\n')

        threads = [threading.Thread(target=feed, args=('M{}'.format(i),))
                   for i in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        records = self.harness.wait(5)
        self.assertEqual(len(records), 5)

    def test_handler_exception_does_not_stop_listener(self):
        def explode(record):
            raise RuntimeError('boom')

        self.harness.listener.server.on_record = explode
        self.harness.send(b'{"model":"A","id":1}\n')
        time.sleep(0.3)

        self.harness.listener.server.on_record = self.harness._on_record
        self.harness.send(b'{"model":"B","id":2}\n')
        records = self.harness.wait(1)
        self.assertEqual(records[0]['model'], 'B')


class TestAccessControl(unittest.TestCase):

    def test_disallowed_peer_is_refused(self):
        harness = ListenerHarness(allow_from=['10.99.'])
        try:
            harness.send(b'{"model":"A","id":1}\n')
            time.sleep(0.3)
            self.assertEqual(harness.records, [])
        finally:
            harness.stop()

    def test_allowed_peer_is_accepted(self):
        harness = ListenerHarness(allow_from=['127.0.0.1'])
        try:
            harness.send(b'{"model":"A","id":1}\n')
            self.assertEqual(len(harness.wait(1)), 1)
        finally:
            harness.stop()


if __name__ == '__main__':
    unittest.main()
