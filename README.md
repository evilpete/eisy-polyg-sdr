# SDR rtl_433 plugin for EISY / Polyglot v3

Presents devices decoded by [rtl_433](https://github.com/merbanan/rtl_433) as
nodes on an EISY (or Polisy) running Polyglot v3.

The plugin opens a TCP port and waits. An rtl_433 process — on the EISY
itself or on any other machine on the network — connects in and streams one
JSON object per line. Each transmitting device becomes an IoX node whose
drivers can be used in programs like any other sensor.

No broker, no MQTT, no cloud service. The listener is
`socketserver` + `json` from the standard library; the only dependency is
`udi_interface` itself.

---

## Wire protocol

This is the entire contract between your sender and the plugin.

- **Transport**: TCP. The plugin listens; the sender connects.
- **Framing**: one JSON object per line, terminated by `\n`.
- **Encoding**: UTF-8.
- **Direction**: one-way. The plugin never writes back to the socket.

Anything beyond that is tolerated rather than required:

| Situation | Behavior |
|---|---|
| Blank lines | Ignored. |
| Unparseable line | Dropped, counted, logged at debug. The connection stays open. |
| Line longer than 8192 bytes | Dropped, counted. The connection stays open. |
| Text before the `{` (e.g. a syslog prefix) | Stripped — everything from the first `{` is parsed. |
| JSON that is not an object | Dropped, counted. |
| Multiple simultaneous senders | Allowed. Two radios can feed one plugin. |
| Sender disconnects | Logged; the plugin keeps listening for the next connection. |

A sender is therefore as simple as:

```python
sock = socket.create_connection(('eisy.local', 1433))
sock.sendall(json.dumps(record).encode() + b'\n')
```

`tools/replay.py` in this repo is a complete working sender you can read or
crib from, and `SENDER.md` outlines what a production sender should handle
(reconnects, supervising rtl_433, buffering) and why the feed is a TCP
stream rather than HTTP posts.

The simplest real feed needs no script at all:

```sh
rtl_433 -F json | nc eisy.local 1433
```

To survive restarts on either end, wrap it in a retry loop:

```sh
while true; do
    rtl_433 -F json | nc eisy.local 1433
    sleep 10
done
```

Add `-M level` to rtl_433 if you want the signal-strength driver populated.

---

## Which fields are used

Devices are classified by the fields they carry, not by a hard-coded list of
model names, so a sensor this plugin has never seen still lands on a
reasonable node type. Field names are matched case-insensitively (`Humidity`
and `humidity` both work).

| Node type | Chosen when the record has | Drivers |
|---|---|---|
| **SDR Temperature Sensor** (`rtemp`) | `temperature_C` / `temperature_F` / `humidity` | Temperature, Humidity, Battery |
| **SDR Energy Monitor** (`rpower`) | `current` / `power_W` / `energy_kWh` | Current, Power, Total Energy, Battery |
| **SDR Tire Pressure** (`rtpms`) | `type: "TPMS"` or a `pressure_*` field | Pressure, Temperature, Battery |
| **SDR Device** (`rgen`) | anything else | Battery only |

Every node also carries:

- **Online** — `1` while the device has been heard within `offline_timeout`,
  `0` after that. Readings are *retained*, not zeroed, so a program can tell
  "20.6 °F and stale" from "0 °F".
- **Last Seen** — seconds since the last transmission.
- **Packets** — transmissions received since the plugin started.
- **Signal** — RSSI, when rtl_433 reports it.

`battery_ok`, `battery` and `maybe_battery` are all mapped onto the Battery
driver as 0 or 100 (rtl_433 reports a flag, not a level). `OK`/`LOW` string
forms are understood too.

Values from a partial transmission are merged, never overwritten with
blanks: if one packet carries temperature only, the humidity from the
previous packet is preserved.

---

## Node identity and addressing

A device is identified by **model + id + channel** — `id` alone is not
unique, since rtl_433 ids collide freely across decoders and multi-channel
sensors reuse an id per channel.

The IoX node address is derived from that triple, readable when it fits:

| Device | Address |
|---|---|
| `LaCrosse-TX141Bv3` id 237 channel 1 | `lacr_237_1` |
| `Efergy-e2CT` id 16386 | `efer_16386` |
| `Hyundai-VDO` id `5173f60b` | `hyun_5173f60b` |

Longer identities fall back to a deterministic hash so the address stays
inside the 14-character limit and stays stable across restarts.

Discovered devices are saved in the plugin's custom data, so nodes are
rebuilt at startup without waiting to hear each device again.

---

## Installation

1. In the Polyglot dashboard, install this plugin from the store, or add it
   manually from `https://github.com/evilpete/eisy-polyg-sdr`.
2. Set the custom parameters (below) — the defaults work as-is.
3. Point an rtl_433 feed at the EISY on the configured port.

Nodes appear as devices are heard. There is nothing to pair or discover.

---

## Configuration

All settings are Polyglot custom parameters. See `POLYGLOT_CONFIG.md`, which
is also rendered in the Polyglot UI.

| Parameter | Default | Meaning |
|---|---|---|
| `port` | `1433` | TCP port to listen on |
| `bind` | `0.0.0.0` | Interface to bind; `127.0.0.1` for a local-only feed |
| `units` | `F` | `F` for °F and PSI, `C` for °C and kPa |
| `offline_timeout` | `3600` | Seconds of silence before a node reports offline |
| `immediate` | `false` | Report as packets arrive instead of only on shortPoll |
| `min_interval` | `10` | With `immediate`, minimum seconds between reports per node |
| `allow_from` | *(empty)* | Comma-separated IPs or prefixes permitted to connect |
| `ignore_models` | *(empty)* | Comma-separated model names to drop |
| `ignore_ids` | *(empty)* | Comma-separated device ids to drop |
| `include_models` | *(empty)* | If set, an allow-list — everything else is dropped |
| `min_rssi` | *(empty)* | Drop packets weaker than this (needs rtl_433 `-M level`) |

### A note on update rate

Readings are cached as packets arrive and flushed to IoX on **shortPoll**
(60 s by default). This is deliberate: an Efergy transmits every 6 seconds
and TPMS sensors send bursts of repeats, and pushing every packet straight
through would flood IoX for no benefit. Raise or lower shortPoll to change
the update rate, or set `immediate` if you want low latency on a quiet band.

The longPoll interval forces a full re-report of every driver, so IoX cannot
drift out of sync.

### Neighbors

With auto-discovery on, anything in range becomes a node — including your
neighbors' sensors. `ignore_models`, `ignore_ids`, and `min_rssi` exist to
prune them; `include_models` turns the plugin into a strict allow-list.

---

## Testing without a radio

`tools/replay.py` streams the sample records in `tools/samples.jsonl` at a
running plugin:

```sh
# one pass of each sample device
./tools/replay.py --host eisy.local

# continuous, with values jittered each pass
./tools/replay.py --host eisy.local --loop --interval 5
```

The offline test suite needs no EISY and no `udi_interface` — it stubs the
interface and drives the controller through a real socket:

```sh
python3 -m unittest discover -s tests -t .
```

It covers classification, unit conversion, address allocation, the listener's
framing and error tolerance, node creation, the offline timeout, the filters,
and registry persistence across a restart.

---

## Troubleshooting

**No nodes appear.** Check the Polyglot log for `listening for JSON on`. If
you see a bind error, another process holds the port. Confirm the feed
arrives with `nc -v eisy.local 1433 < /dev/null`, and check `allow_from` if
it is set.

**Nodes appear but values never update.** Values flush on shortPoll — wait
one interval, or use the controller's *Refresh All* command.

**A node shows old values and Online = 0.** That device has not been heard
for `offline_timeout` seconds. The last reading is kept on purpose.

**Temperature reads in the wrong unit.** Set `units`, then restart the
plugin so every node re-reports with the new UOM.

---

## License

MIT — see `LICENSE`.
