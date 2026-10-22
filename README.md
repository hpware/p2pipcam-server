# p2pipcam replacement server

English | [繁體中文](README.zh-TW.md)

A dependency-free, self-hostable replacement for the dead `www.p2pipcam.com`
cloud used by the old "P2P IP Cam Lite" / I-Bt IBT-200CP camera family.

If your 2010-2012-era IP camera only speaks the ancient TUTK UDP protocol and
its cloud is gone, point its old hostname at your own box running this server
and take the videos and photos back.

## What it does

| File (`re/`) | Purpose |
|---|---|
| `p2p_protocol.py` | Packet header, message types, query/hole encode/decode |
| `p2p_server.py`   | UDP server on ports 30000/30002/30003/30004, relay or direct mode, admin dashboard, Prometheus metrics |
| `fake_cloud.py`   | Stand-in for `www.p2pipcam.com/api/userip.asp` (camera self-registration) |
| `camera_state.py` | Read-only UDP 10139 state/cloud-request dumper |
| `p2p_client.py`   | Reference query / hole-punch / HTTP client |
| `download_geoip.py` | Sidecar that keeps a MaxMind GeoLite2-Country CSV (optional, powers country labels) |
| `selftest.py`     | End-to-end test without a camera |

The wire protocol is documented in [`notes/REPORT.md`](notes/REPORT.md).

## Docker quick start

```sh
cd deploy
cp ../re/README.md .  # optional
cat > .env <<'EOF'
PUBLIC_HOST=cameras.example.com
MAXMIND_URL=https://USER:KEY@download.maxmind.com/geoip/databases/GeoLite2-Country-CSV/download?suffix=zip
EOF
docker compose up -d --build
```

Then point the host name your camera ROM uses (typically `www.p2pipcam.com`)
via a **plain DNS A record** at your server. Port forwards needed:

| Port | Transport | Purpose |
|---|---|---|
| 80 | TCP + UDP | camera self-registration (`/api/userip.asp`, raw UDP) |
| 30000 | UDP | TUTK discovery |
| 30002 | UDP | device query (`0x60` → `0x61`/`0x63`) |
| 30003 | UDP | hole-punch (`0x02`/`0x04` → `0x06`) |
| 30004 | UDP | relay channel (`0x64` → `0x66`) |

CDNs/reverse proxies do **not** forward UDP, so a DNS-only record is required.

## Run without Docker

```sh
cd re
sudo python3 fake_cloud.py --port 80 --body source --save registrations.jsonl
python3 p2p_server.py --public-host cameras.example.com -v
python3 selftest.py  # stop the server first
```

## Admin / monitoring

- `http://localhost:9090/` — auto-refreshing dashboard (cameras, phone
  sessions, countries via GeoLite2)
- `/api/connections` — full JSON snapshot
- `/api/health` — tiny health check
- `/metrics` — Prometheus text format for Grafana
- `/api/stats` — public counter (CORS `*`): how many cameras/phones have
  relied on this server in the last 1h/24h/7d/30d

Add `--admin-token ...` and use `Authorization: Bearer ...` /
`?token=` for anything other than `/metrics` and `/api/stats`.

## Security note

This protocol is from ~2010: **every credential travels in cleartext.** Camera
registration sends the admin password as an HTTP GET parameter, query/hole
packets carry the 8-character ID+password in plain ASCII, and video commands
use reversible Basic auth. Host the server yourself so only *your* traffic
passes it, and put it behind a firewall/LAN where possible. Logs are scrubbed
of `userpwd` in line output; the JSONL files intentionally keep the identity
(device ID) but drop secrets — check the code before enabling any install to
keep it that way.

## Status

* 2010 and 2012 Android clients were recovered and their packets documented.
* The protocol was reimplemented from ROM strings and packet captures.
* Relay mode passes `selftest.py`; live-testing against a physical IBT-200CP
  shows registrations arriving the moment it is DNS-pointed at the server.
* Camera-side registration/keepalive formats beyond the captured ones are
  still guessable-but-unconfirmed; unknown packets are logged as hex.

## Ethics

This is a recovery tool for abandoned hardware you own. It deliberately does
not implement camera takeover; when a stranger's camera registers, the server
only logs its device ID. Full credentials are redacted from the persistent
logs. Please keep it that way.

## License

[MIT](LICENSE)
