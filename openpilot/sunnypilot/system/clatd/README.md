# clatd — 464XLAT for IPv6-only cellular bearers

Restores IPv4 connectivity on cellular SIMs that hand out an IPv6-only data
context (Google Fi data-only, T-Mobile prepaid, and other carriers running
NAT64/PLAT). Without this, the bearer comes up and is assigned an IPv4 address,
but only ICMPv4 passes — TCP to any IPv4 destination silently times out
because there's no real IPv4 route through the carrier's gateway.

## How it works

Runs a userspace [`tayga`](https://github.com/apalrd/tayga) translator on a TUN
device (`clat0`):

- Local apps that try to reach an IPv4 destination get routed into `clat0`
  (default IPv4 route via `clat0` at metric 800 — loses to wlan0 at 600,
  wins over the broken native ppp0 default at 1000).
- `tayga` translates each packet to IPv6 with the well-known NAT64 prefix
  (`64:ff9b::/96`, RFC 6052) and sends it out the regular IPv6 default route.
- The carrier's PLAT translates back to real IPv4 at its gateway.
- Return path is the inverse — incoming IPv6 destined for our CLAT endpoint
  address gets pulled into `clat0` and translated back to IPv4.

The CLAT IPv6 endpoint is claimed from the bearer's delegated `/64` (suffix
`feed:c1a7`). When the modem renegotiates and the prefix changes, the
supervisor tears down and re-establishes with the new prefix.

## Layout

| Path | Role |
|---|---|
| `sunnypilot/clatd.py` | File-based state helpers (paths, enabled flag, install flag) |
| `sunnypilot/system/clatd/installer.py` | Builds tayga from a pinned commit of apalrd/tayga (~5s, needs internet) |
| `sunnypilot/system/clatd/manage_clatd.py` | `PythonProcess` supervisor — self-gates, watches bearer, runs tayga |
| `/data/clat/bin/tayga` | Built tayga binary (~150 KB, dynamic-linked to AGNOS libc) |
| `/data/clat/run/tayga.conf` | Generated each cycle from the current bearer prefix |
| `/data/clat/state/` | Tayga's persistent state dir (dynamic-pool maps, currently unused) |
| `/data/clat/enabled` | File-based enable flag |
| `/data/clat/install_requested` | File-based one-shot install request |

## Enabling

CLAT is off by default. To enable:

```bash
# 1. Request install (supervisor will build tayga on next loop iteration)
touch /data/clat/install_requested
# 2. Enable
touch /data/clat/enabled
```

Watch progress:

```bash
grep -E manage_clatd /data/log/swaglog.* | tail -20
```

To disable:

```bash
rm /data/clat/enabled
```

To uninstall:

```bash
rm -rf /data/clat/bin
```

## Prerequisites

- AGNOS toolchain (`gcc`, `make`) — present on stock comma-four
- `/dev/net/tun` — present on stock kernel
- Passwordless `sudo` for the `comma` user (already used by `manage_tailscaled`)
- Carrier must run a NAT64/PLAT honoring the well-known prefix `64:ff9b::/96`.
  Confirmed on Google Fi (T-Mobile underlay). Other carriers may use a
  carrier-specific prefix — `NAT64_PREFIX` in `sunnypilot/clatd.py` would need
  to be made configurable.

## Background

The textbook signature on a misconfigured bearer is: ICMPv4 succeeds, TCPv4
times out to any destination. T-Mobile (and Google Fi by extension) increasingly
uses 464XLAT, expecting the device to run a CLAT. Android, iOS, and most
cellular routers run one automatically; AGNOS does not.

See [RFC 6877 (464XLAT)](https://datatracker.ietf.org/doc/html/rfc6877)
and [RFC 6052 (NAT64 address format)](https://datatracker.ietf.org/doc/html/rfc6052)
for the underlying mechanism.
