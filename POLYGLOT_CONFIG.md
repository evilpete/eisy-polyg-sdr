## SDR rtl_433

This plugin listens on a TCP port for line-delimited JSON produced by
[rtl_433](https://github.com/merbanan/rtl_433) and creates a node for each
device it hears.

Point a feed at this machine, for example:

```
rtl_433 -F json | nc <eisy-address> 1433
```

Nodes are created automatically as devices transmit. Nothing needs to be
paired or discovered.

### Parameters

**port** — TCP port to listen on. Default `1433`.

**bind** — Address to bind. Default `0.0.0.0` (all interfaces). Use
`127.0.0.1` if rtl_433 runs on this machine and you do not want the port
exposed to the network.

**units** — `F` for Fahrenheit and PSI, `C` for Celsius and kPa.
Default `F`. Restart the plugin after changing this so every node
re-reports with the new unit.

**offline_timeout** — Seconds of silence before a node reports Online = 0.
Default `3600`. The last reading is retained; only the Online flag changes,
so a program can distinguish a stale value from a real one.

**immediate** — `true` to report readings as packets arrive rather than
waiting for the next shortPoll. Default `false`. Leave this off unless you
need low latency: a busy 433 MHz band produces a great many packets.

**min_interval** — With `immediate` enabled, the minimum number of seconds
between reports for any one node. Default `10`.

**allow_from** — Comma-separated list of IP addresses or address prefixes
allowed to connect, e.g. `192.168.1.50,10.0.0.`. Empty (the default) accepts
any host.

**ignore_models** — Comma-separated rtl_433 model names to discard, e.g.
`Acurite-Tower,Nexus-TH`. Useful for pruning neighbors' sensors.

**ignore_ids** — Comma-separated device ids to discard.

**include_models** — If set, only these model names are accepted and
everything else is dropped. Turns auto-discovery into a strict allow-list.

**min_rssi** — Drop packets weaker than this signal level. Requires running
rtl_433 with `-M level`, otherwise no packet carries a signal reading and
this has no effect.

### Controller drivers

- **Devices** — nodes currently known
- **Packets Received** — records accepted since start
- **Feeds Connected** — active TCP connections
- **Packets Dropped** — records rejected by a filter or unparseable

### Commands

- **Refresh All** — force every node to re-send all of its drivers.
