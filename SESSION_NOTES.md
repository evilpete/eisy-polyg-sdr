# Session notes — 2026-08-08

State and context for picking this project back up in a later session.

## What this is

A Polyglot v3 plugin for the EISY that receives rtl_433 JSON over a TCP
connection and represents each transmitting device as an IoX node. First of
three planned plugins (see *Future work*).

Repo: `evilpete/eisy-polyg-sdr`, branch `main`.

## Decisions made this session

Answered explicitly:

| Question | Decision |
|---|---|
| Transport | **TCP server** — the plugin listens, the sender connects. Not MQTT: no broker, lightweight, standalone. |
| Discovery | **Auto-add everything heard.** Filters exist for pruning neighbors. |
| Node types | **Per-device-class nodedefs** (thermo / energy / TPMS / generic), chosen by which fields a record carries, not by model name. |
| Repo | Standalone `evilpete/eisy-polyg-sdr`. |
| Sender | Peter writes his own script. The wire contract is documented in README under *Wire protocol*. |

Chosen as defaults (the follow-up question round was skipped — revisit if any
of these are wrong):

- **Units**: `F` by default, with a `units` parameter for `C`. Temperature and
  pressure carry two ranges in the editor and the UOM is switched at runtime.
- **Staleness**: age driver + online flag, `offline_timeout` default 1 hour.
  Values are retained when stale, never zeroed. Nodes are never auto-deleted.
- **Extra drivers**: battery, signal (RSSI), per-node packet count, and
  controller-level totals — all four were implemented.

## Layout

```
rtl433-poly.py         entry point (server.json executable)
nodes/controller.py    config, listener lifecycle, device registry, polling
nodes/sensor.py        RtlNode base + Thermo / Energy / Tpms / Generic
rtl433/listener.py     TCP line-delimited JSON server (stdlib only)
rtl433/classify.py     record -> device class, identity, address, values
rtl433/units.py        conversions and ISY UOM constants
profile/               nodedefs, editors, nls
tools/replay.py        sample-record sender for testing without a radio
tests/                 offline suite; stub_udi.py stands in for udi_interface
```

Data flow: listener thread → `Controller.ingest` (classify, filter, cache on
the node) → creator thread (serialized `addNode`, which is async and must be
awaited) → poll thread (`report`, flushing cached values to IoX).

Readings are cached on arrival and flushed on shortPoll rather than pushed
per packet — the Efergy transmits every 6 s and TPMS sends bursts of repeats.

## Test status

35 tests, passing, stable over repeated runs:

```sh
python3 -m unittest discover -s tests -t .
```

`udi_interface` will not install in a plain container (its `netifaces`
dependency needs build tools), so `tests/stub_udi.py` reimplements the parts
of the interface the plugin uses — the subscribe model, the per-instance deep
copy of `drivers`, the `setDriver` signature including the uom override, and
the `addNode`/`ADDNODEDONE` handshake. The stub raises on any driver not
declared for the node's nodedef, which catches profile drift between
`sensor.py` and `nodedefs.xml`.

Two real bugs were found and fixed by these tests: the online flag was
decided from the truncated integer age (a node stayed online a second too
long), and packets arriving before a node finished being created were merged
into a single ingest, under-counting the per-node packet total.

## To verify on hardware

Nothing below is known-broken; these are the points that could not be
confirmed without an EISY.

1. **Decimal display.** Editors declare `prec="1"`; the interface sends
   values as strings. Confirm 20.6 °C shows as `69.1`, not `691` or `69`.
   If wrong, the fix is either dropping `prec` or pre-scaling the value.
2. **kPa UOM.** `units.UOM_KPA = 31` came from a search summary — the UD wiki
   and `developer.isy.io` are both blocked from this environment, so the
   table was never read directly. Only matters in `units=C` mode. PSI (138),
   °F (17), °C (4), % (22/51), amp (1), watt (73), kWh (33), seconds (58) and
   raw (56) are the conventional values used across published plugins.
3. **`ICON` names** in `profile/nls/en_us.txt` (`TempSensor`,
   `EnergyMonitor`, `Sensor`, `Input`). An unrecognized icon name falls back
   to a default rather than failing, so this is cosmetic.
4. **Address length.** Addresses are capped at 14 characters; IoX on eisy may
   allow more. If longer is fine, raise `classify.MAX_ADDRESS_LEN` so fewer
   devices fall back to hashed addresses.
5. **Restart behavior.** Confirm nodes come back from the saved registry
   without waiting to hear each device again, and that renaming a node in the
   admin console survives a restart.

Quickest smoke test after install:

```sh
./tools/replay.py --host <eisy> --loop --interval 5
```

Three nodes should appear (`lacr_237_1`, `efer_16386`, `hyun_5173f60b`) plus
two more from the extra samples, and values should update each shortPoll.

## Open questions for next session

- Is the shortPoll flush rate right in practice, or should `immediate` be the
  default?
- Should the Efergy `current` be converted to watts (`current × mains
  voltage`) so the Power driver is populated? That needs a nominal voltage
  parameter.
- Any interest in auto-deleting nodes not heard for N days? Currently they
  persist and just report offline.

## Future work

Two further plugins were mentioned, not started:

- Midea "DUO" air conditioner via
  [msmart-ng](https://github.com/0xbw/midea-msmart).
- EISY FreeBSD host system status.

Both are conventional polling plugins and can reuse the controller/registry
shape here, minus the listener.
