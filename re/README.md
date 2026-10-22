# Reversed P2P server / client for the old `p2pipcam.com` cameras

This directory contains a dependency-free implementation of the UDP
rendezvous protocol used by the IBT-200CP / “P2P IP Cam Lite” camera family.

## Files

| File | Purpose |
|---|---|
| `p2p_protocol.py` | Packet header, message types, query/hole encode/decode |
| `p2p_server.py` | UDP server on ports 30000/30002/30003/30004 |
| `p2p_client.py` | Reference query / hole-punch / HTTP client |
| `selftest.py` | Starts the server, fakes a camera, verifies relaying |
| `camera_state.py` | Read-only UDP 10139 state/cloud-request dumper |
| `fake_cloud.py` | Stand-in for `www.p2pipcam.com/api/userip.asp` |
| `camera.local.json` | Live camera credentials (do not commit) |

## Run

```sh
cd re
python3 fake_cloud.py --port 80 --body "" --save registrations.jsonl
python3 p2p_server.py --public-host www.p2pipcam.com -v
```

`fake_cloud.py` listens on TCP and UDP port 80; the camera sends the raw
`GET /api/userip.asp` request over UDP. If nginx already owns TCP 80:

```sh
# UDP 80, alongside nginx:
sudo python3 fake_cloud.py --port 80 --no-tcp --body "" --save registrations.jsonl
# TCP 80 via nginx (see nginx-www.p2pipcam.com.conf):
python3 fake_cloud.py --port 8080 --no-udp --body "" --save registrations.jsonl
```

Use a **DNS-only** record for `www` (no Cloudflare proxy): the camera needs
UDP 80 and UDP 30000-30004, which a normal HTTP CDN does not pass. Then
power-cycle the camera and watch both logs.

The live camera's state can be read without changing anything:

```sh
python3 camera_state.py 172.19.212.145
```

`--public-host` must resolve to this machine from both the phone and the
camera. The server prints every packet; unknown packets are shown as hex so
the camera-registration format can be added.

### Relay mode (default)

The phone is told the camera lives at the server. The server forwards packets
between them. This is the easiest mode behind NAT.

### Direct mode

```sh
python3 p2p_server.py --public-host www.p2pipcam.com --mode direct
```

The phone is told the camera’s last-known public address. The server only
answers discovery and hole-punch requests.

### Pin a camera

If auto-learning does not see the camera’s registration packet:

```sh
cp devices.example.json devices.json
$EDITOR devices.json
python3 p2p_server.py --public-host www.p2pipcam.com --devices devices.json -v
```

## Admin monitoring

`p2p_server.py` starts a read-only dashboard on `http://127.0.0.1:8081/`:

- `/` — auto-refreshing HTML table of cameras and phone sessions
- `/api/connections` — JSON snapshot for the iOS admin screen
- `/api/health` — small health check

By default it binds loopback only. To view it from the phone:

```sh
python3 p2p_server.py --public-host www.p2pipcam.com \
  --admin-bind 0.0.0.0 --admin-token 'change-me' -v
```

Then open `http://<server>:8081/?token=change-me` or send
`Authorization: Bearer change-me`. Put it behind HTTPS/reverse proxy if it is
exposed to the internet. The dashboard is read-only: no reboot, upgrade, or
camera-control actions.

The iOS app's admin tab should call `/api/connections` with the bearer token
and show cameras, sessions, last-seen ages, and packet/byte counters.

## Test

```sh
python3 selftest.py
```

Expected:

```
query: {'dev_id': 'TEST1234', 'p2p1': '127.0.0.1', ...}
hole : {'dev_id': 'TEST1234', 'ip': '127.0.0.1', 'port': 30003, ...}
camera got: 42 b'GET /cmmd=52...'
client got: 42 b'HTTP/1.1 200 OK...'
SELFTEST OK
```

## Packet summary

```
0   1  version 0xA0
1   1  type
2   2  payload length, big-endian
4   4  seq
8   4  ack
12  N  payload
```

* Query: `0x60` to UDP 30002, 40-byte id+password.
  * Old answer: `0x61`, 100 bytes (id 20 + p2p 40 + relay 40).
  * New answer: `0x63`, 180 bytes (id 20 + p2p1 40 + relay1 40 + p2p2 40 + relay2 40).
* Hole: `0x02` (new) or `0x04` (old) to UDP 30003.
  * Answer: `0x06`, 40 bytes (id 20 + IPv4 LE 4 + port LE 2).
* Relay: `0x64` to UDP 30004, answer `0x66`.
* Data: `0x2A` (HTTP over UDP), `0x21/0x22/0x23` (JPEG), `0x2C` (ACK).

See `../notes/REPORT.md` for the full protocol, evidence, and firmware notes.

## Known gaps

* Camera registration/keepalive format is not confirmed; auto-learn and
  `devices.json` cover it.
* `0x03` HPCMD is not implemented (the relay mode does not need it).
* The server has only been tested against synthetic packets, not a physical
  IBT-200CP. Capture a boot trace and adjust if the camera speaks a variant.
