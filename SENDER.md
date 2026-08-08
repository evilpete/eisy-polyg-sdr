# Writing the sender

The plugin accepts **raw newline-delimited JSON over a persistent TCP
connection**. This document outlines what a sender has to do, and why that
shape rather than HTTP.

## Why not HTTP POST

HTTP was considered and rejected for this feed:

- **rtl_433 already emits exactly the right thing.** `rtl_433 -F json` writes
  one JSON object per line to stdout. Piping that into a socket needs no
  transformation and, in the simplest case, no script at all. An HTTP sender
  has to frame every record itself.
- **Per-packet overhead is disproportionate.** A decoded record is 100–300
  bytes. A POST wraps each one in request line, headers, and a response —
  several times the payload — and a busy 433 MHz band produces a steady
  stream of them. One long-lived TCP connection costs a `send()` per record.
- **It would put a web server in the plugin.** The listener is 150 lines of
  `socketserver` with no dependencies. An HTTP endpoint means request
  parsing, method and content-type handling, status codes, and a much larger
  surface on a port exposed to the LAN.
- **Nothing needs a reply.** The feed is one-way and fire-and-forget. HTTP's
  request/response cycle buys nothing here; a stale reading is handled by the
  offline timeout, not by retries.

The one thing HTTP would give you is a per-record acknowledgement. That is
not worth having: if a packet is lost, the sensor transmits again in seconds.

## The contract

Everything the sender must do:

1. Connect to the plugin's TCP port (default 1433).
2. Write one JSON object per line, UTF-8, `\n`-terminated.
3. Reconnect if the connection drops.

That is the whole protocol. The plugin never writes back. See the tolerance
table in `README.md` for how malformed input is handled — in short, a bad
line is dropped and the connection survives.

## Option 1 — no script

```sh
while true; do
    rtl_433 -F json | nc eisy.local 1433
    sleep 10
done
```

Worth starting here. If this works, a script only buys you nicer logging and
restart behavior.

Add `-M level` if you want the Signal driver populated.

## Option 2 — a supervised sender

Useful once it should survive reboots and radio hiccups. Outline:

```
main():
    loop forever:
        start rtl_433 as a subprocess with -F json, stdout as a pipe
        loop forever:
            read a line from rtl_433
            if EOF: break            # radio died, restart it
            ensure_connected()       # dial the plugin if we have no socket
            send line + newline
            on send failure: drop the socket, keep the line or discard it
        reap the subprocess, back off, restart

ensure_connected():
    if socket is open: return
    try to connect
    on failure: sleep with exponential backoff (1s, 2s, 4s ... cap ~60s)
```

Points worth getting right:

- **Pass records through unmodified.** Do not reformat, round, or rename
  fields — the plugin's classification depends on rtl_433's own field names,
  and it already handles case differences and missing fields.
- **Line-buffer the pipe.** Give the subprocess `bufsize=1` and text mode, or
  records arrive in 4 KB clumps and updates look laggy.
- **Never let a send error kill the reader.** Close the socket, mark it dead,
  and let `ensure_connected()` retry on the next line. The pipe from rtl_433
  must keep being drained or the subprocess blocks on write.
- **Do not buffer records while disconnected**, or at most keep the last few.
  A sensor that transmits every 30 seconds makes a backlog worthless — by the
  time the plugin reconnects, the readings are stale and the fresh ones are
  right behind.
- **Read rtl_433's stderr separately** (or send it to a log). Leaving it
  attached to a full pipe is a classic way to wedge a subprocess.
- **Use TCP keepalive** (`SO_KEEPALIVE`) so a silently dropped connection —
  a rebooted EISY, a NAT timeout — is noticed on a quiet band instead of the
  socket looking open forever.

## Option 3 — no subprocess

If rtl_433 already runs under a service manager, have it write to a FIFO or
to stdout and let the sender read stdin:

```sh
rtl_433 -F json | ./sender.py --host eisy.local --port 1433
```

The sender then only handles the socket half — connect, write lines,
reconnect on failure. This is the smallest useful version and is easy to
supervise with rc.d, systemd, or supervisord.

## Reference

`tools/replay.py` is a complete working sender, minus the rtl_433
subprocess: it reads records from a file and streams them to the plugin.
The socket half is the part worth copying.

## Testing your sender

Point it at a plain listener before involving the EISY:

```sh
nc -l 1433
```

Every line your sender writes should appear, one JSON object per line, with
nothing else interleaved. Then point it at the plugin and watch the
Polyglot log for `feed connected from`.
