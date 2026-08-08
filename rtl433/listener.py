"""
Line-delimited JSON TCP listener.

The plugin binds a TCP port and waits; the rtl_433 side connects in and
streams one JSON object per line, e.g.

    rtl_433 -F json | nc eisy.local 1433

Multiple concurrent senders are allowed, so a second radio on another host
can feed the same plugin.  Standard library only -- no broker, no client
library, nothing to install.
"""

import json
import socket
import socketserver
import threading

# A single rtl_433 record is a few hundred bytes.  Anything beyond this is a
# desynchronized stream or a wrong protocol on the port; the line is dropped
# rather than buffered.
MAX_LINE = 8192


class _JsonLineHandler(socketserver.StreamRequestHandler):
    # Blocking reads: a sensor may legitimately be quiet for a long time and
    # the connection should survive that.
    timeout = None

    def handle(self):
        server = self.server
        peer = self.client_address[0]

        if not server.peer_allowed(peer):
            server.log.warning('rtl_433: rejected connection from %s '
                               '(not in allow_from)', peer)
            return

        server.connection_opened(peer)
        try:
            while not server.stopping:
                line = self.rfile.readline(MAX_LINE)
                if not line:
                    break                      # peer closed
                if len(line) >= MAX_LINE and not line.endswith(b'\n'):
                    server.log.warning('rtl_433: oversize line from %s, '
                                       'dropping', peer)
                    server.count_error()
                    continue
                self._dispatch(line, peer)
        except (OSError, socket.error) as err:
            server.log.debug('rtl_433: connection from %s ended: %s', peer, err)
        finally:
            server.connection_closed(peer)

    def _dispatch(self, line, peer):
        server = self.server
        text = line.decode('utf-8', errors='replace').strip()
        if not text:
            return

        # Tolerate a syslog-framed stream ("<133>Aug 4 ... {json}") so that
        # switching rtl_433 to -F syslog does not silently break the feed.
        if not text.startswith('{'):
            brace = text.find('{')
            if brace < 0:
                server.count_error()
                return
            text = text[brace:]

        try:
            record = json.loads(text)
        except ValueError:
            server.log.debug('rtl_433: unparseable line from %s: %.120s',
                             peer, text)
            server.count_error()
            return

        if not isinstance(record, dict):
            server.count_error()
            return

        try:
            server.on_record(record)
        except Exception:                      # never kill the reader thread
            server.log.exception('rtl_433: handler failed for record from %s',
                                 peer)


class Rtl433Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, bind, port, on_record, log, allow_from=None):
        self.on_record = on_record
        self.log = log
        self.allow_from = list(allow_from or [])
        self.stopping = False

        self.lock = threading.Lock()
        self.connections = 0
        self.total_connections = 0
        self.errors = 0

        socketserver.ThreadingTCPServer.__init__(
            self, (bind, port), _JsonLineHandler)

    def peer_allowed(self, peer):
        """Empty allow_from means any host; otherwise match IP or prefix."""
        if not self.allow_from:
            return True
        return any(peer == entry or peer.startswith(entry)
                   for entry in self.allow_from)

    def connection_opened(self, peer):
        with self.lock:
            self.connections += 1
            self.total_connections += 1
        self.log.info('rtl_433: feed connected from %s (%d active)',
                      peer, self.connections)

    def connection_closed(self, peer):
        with self.lock:
            self.connections = max(0, self.connections - 1)
        self.log.info('rtl_433: feed from %s disconnected (%d active)',
                      peer, self.connections)

    def count_error(self):
        with self.lock:
            self.errors += 1

    def handle_error(self, request, client_address):
        self.log.exception('rtl_433: unhandled error serving %s',
                           client_address)


class Listener:
    """Owns the server thread and its lifecycle."""

    def __init__(self, log):
        self.log = log
        self.server = None
        self.thread = None
        self.bound = None

    @property
    def active_connections(self):
        return self.server.connections if self.server else 0

    @property
    def errors(self):
        return self.server.errors if self.server else 0

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, bind, port, on_record, allow_from=None):
        """(Re)bind the listener.  Returns True on success."""
        if self.is_running():
            if self.bound == (bind, port):
                return True
            self.stop()

        try:
            self.server = Rtl433Server(bind, port, on_record, self.log,
                                       allow_from)
        except OSError as err:
            self.log.error('rtl_433: cannot bind %s:%s -- %s', bind, port, err)
            self.server = None
            return False

        self.thread = threading.Thread(
            target=self.server.serve_forever, name='rtl433-listener',
            kwargs={'poll_interval': 0.5})
        self.thread.daemon = True
        self.thread.start()
        self.bound = (bind, port)
        self.log.info('rtl_433: listening for JSON on %s:%s', bind, port)
        return True

    def stop(self):
        if self.server is not None:
            self.server.stopping = True
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                self.log.exception('rtl_433: error closing listener')
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.server = None
        self.thread = None
        self.bound = None
