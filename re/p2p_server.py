#!/usr/bin/env python3
"""A small UDP rendezvous/relay server for the old "P2P IP Cam Lite" cameras.

It speaks the protocol used by www.p2pipcam.com (UDP 30000-30004) and can run
in two modes:

  relay  (default) - tell the phone that the camera is at this server, then
                     forward packets between phone and camera.  Works behind
                     NAT on both sides and is the easiest mode to test.

  direct           - tell the phone the camera's real public address.  The
                     camera and phone then talk directly; the server only
                     answers the query/hole-punch requests.

The camera is learned automatically from the first packet that contains its
8-character device id.  You can also pin devices in a JSON file:

    {"ABC12345": {"host": "192.168.1.50", "port": 30003}}

Run:

    python3 p2p_server.py --public-host www.p2pipcam.com --verbose

Then point www.p2pipcam.com (and/or the hostname used by the firmware) at this
machine.  Watch the log: every unknown packet is printed as hex so the
registration format can be added if a camera uses something unexpected.
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import csv
import html
import ipaddress
import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from p2p_protocol import (
    ACK, AUTREQ, DEVTST, HPCMD, HPFAI, HPPKT, HPREQ, HPSUC, IMGEND, IMGMID,
    IMGSTR, PORT_P2P, PORT_QUERY, PORT_RELAY, PORT_STATUS, QUERYANS, REG,
    REGRS, RLACK, RLEND, RLREQ, RLSTR, SERVERANS, SERVERWHO, TU_DEVADDR,
    TU_DEVADDRR, TU_DEVINFO, TU_DEVINFO2, TU_DEVINFO_R, TU_DEVINFO_R2,
    TU_DEVINFOS, TU_DEVINFOS_R, TU_HELLO1, TU_NATTST, TU_NATTST_R, VERSION,
    WEBDAT, QUERYREQ2, Packet, fixed_str, guess_dev_id, looks_like_dev_id,
    make_hole_success, make_hole_text, make_server_ans, parse_dev_id,
    parse_hole_success, parse_packet, parse_password, parse_server_ans,
)

LOG = logging.getLogger("p2p")


@dataclass
class Camera:
    dev_id: str
    host: str
    port: int
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    last_type: int = 0
    last_port: int = PORT_P2P
    packets_in: int = 0
    bytes_in: int = 0
    packets_out: int = 0
    bytes_out: int = 0


@dataclass
class Session:
    dev_id: str
    app_addr: tuple[str, int]
    server_port: int
    camera_addr: tuple[str, int] | None = None
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    last_type: int = 0
    packets_in: int = 0
    bytes_in: int = 0
    packets_out: int = 0
    bytes_out: int = 0


class P2PServer(asyncio.DatagramProtocol):
    def __init__(self, server: "Server", port: int):
        self.server = server
        self.port = port
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        self.server.handle(self.port, addr, data)

    def error_received(self, exc):
        LOG.warning("socket %s error: %s", self.port, exc)


class Server:
    def __init__(self, args):
        self.args = args
        self.public_host = args.public_host
        self.public_ip = self._resolve(args.public_host)
        self.mode = args.mode
        self.transports: dict[int, asyncio.DatagramTransport] = {}
        self.cameras: dict[str, Camera] = {}
        self.sessions: dict[tuple[str, int], Session] = {}
        self.app_dev: dict[tuple[str, int], str] = {}
        self.started = time.time()
        self.device_log = args.device_log
        self.seen_db = args.seen_db
        self.seen: dict[str, dict] = {}
        self.save_seen = 0.0
        if self.seen_db and os.path.exists(self.seen_db):
            try:
                with open(self.seen_db, encoding="utf-8") as fh:
                    self.seen = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                LOG.warning("reading seen db %s: %s", self.seen_db, exc)
        self.total_packets = 0
        self.total_bytes = 0
        self.total_queries = 0
        self.total_holes = 0
        self.total_relays = 0
        for dev_id, value in (args.devices or {}).items():
            self.cameras[dev_id] = Camera(dev_id, value["host"], int(value.get("port", PORT_P2P)))
            self.mark_seen(dev_id, "camera", value["host"])
            LOG.info("pinned camera %s -> %s:%s", dev_id, value["host"], value.get("port", PORT_P2P))

    @staticmethod
    def _resolve(host: str) -> str:
        try:
            return socket.gethostbyname(host)
        except OSError:
            return host

    def snapshot(self) -> dict:
        now = time.time()
        cameras = []
        for cam in sorted(self.cameras.values(), key=lambda c: c.dev_id):
            iso, name = self.geoip_country(cam.host)
            cameras.append({
                "dev_id": cam.dev_id,
                "address": f"{cam.host}:{cam.port}",
                "country": iso,
                "country_name": name,
                "last_seen_seconds": round(now - cam.last_seen, 1),
                "online": (now - cam.last_seen) < 120,
                "last_type": f"0x{cam.last_type:02X}",
                "last_port": cam.last_port,
                "packets_in": cam.packets_in,
                "bytes_in": cam.bytes_in,
                "packets_out": cam.packets_out,
                "bytes_out": cam.bytes_out,
            })
        sessions = []
        for sess in sorted(self.sessions.values(), key=lambda x: x.last_seen, reverse=True):
            app_iso, app_name = self.geoip_country(sess.app_addr[0])
            sessions.append({
                "dev_id": sess.dev_id,
                "app_address": f"{sess.app_addr[0]}:{sess.app_addr[1]}",
                "country": app_iso,
                "country_name": app_name,
                "server_port": sess.server_port,
                "camera_address": (
                    f"{sess.camera_addr[0]}:{sess.camera_addr[1]}" if sess.camera_addr else None
                ),
                "last_seen_seconds": round(now - sess.last_seen, 1),
                "active": (now - sess.last_seen) < 120,
                "last_type": f"0x{sess.last_type:02X}",
                "packets_in": sess.packets_in,
                "bytes_in": sess.bytes_in,
                "packets_out": sess.packets_out,
                "bytes_out": sess.bytes_out,
            })
        return {
            "uptime_seconds": round(now - self.started, 1),
            "mode": self.mode,
            "public_host": self.public_host,
            "public_ip": self.public_ip,
            "total_packets": self.total_packets,
            "total_bytes": self.total_bytes,
            "total_queries": self.total_queries,
            "total_holes": self.total_holes,
            "total_relays": self.total_relays,
            "cameras": cameras,
            "sessions": sessions,
        }

    def log_device(self, port: int, addr: tuple[str, int], pkt: Packet, body_preview: str):
        """Append one JSON line per source device packet to --device-log."""
        if not self.device_log:
            return
        known = "camera" if any((c.host, c.port) == addr for c in self.cameras.values()) else (
            "phone" if addr in self.sessions else "new")
        dev_id = parse_dev_id(pkt.body)
        if not looks_like_dev_id(dev_id):
            dev_id = self.dev_id_from_http(pkt.body) or self.app_dev.get(addr, "")
        iso, name = self.geoip_country(addr[0])
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "dev_id": dev_id,
            "kind": known,
            "address": f"{addr[0]}:{addr[1]}",
            "country": iso,
            "country_name": name,
            "server_port": port,
            "type": f"0x{pkt.msg_type:02X}",
            "bytes": len(pkt.body) + 12,
            "body": body_preview,
        }
        try:
            with open(self.device_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError as exc:
            LOG.warning("device log %s: %s", self.device_log, exc)

    def send(self, data: bytes, addr: tuple[str, int], port: int | None = None,
             cam: Camera | None = None, sess: Session | None = None):
        transport = self.transports.get(port or PORT_P2P)
        if transport is None:
            LOG.warning("no transport for port %s", port)
            return
        transport.sendto(data, addr)
        if cam is not None:
            cam.packets_out += 1
            cam.bytes_out += len(data)
        if sess is not None:
            sess.packets_out += 1
            sess.bytes_out += len(data)

    # ---------------------------------------------------------------- routing

    def handle(self, port: int, addr: tuple[str, int], data: bytes):
        self.total_packets += 1
        self.total_bytes += len(data)
        pkt = parse_packet(data)
        if pkt is None:
            LOG.debug("ignored %d bytes from %s:%d: %s", len(data), *addr, data[:32].hex())
            return

        if pkt.msg_type in (SERVERWHO, QUERYREQ2, HPREQ, HPPKT, RLREQ):
            body_preview = f"<redacted {len(pkt.body)} bytes id={parse_dev_id(pkt.body)!r}>"
        elif b"Authorization:" in pkt.body:
            body_preview = "<http auth redacted>"
        else:
            body_preview = pkt.body[:64].decode("ascii", "replace").replace("\r", "\\r").replace("\n", "\\n")
        LOG.debug("rx :%d %s:%d type=0x%02X len=%d seq=%d ack=%d body=%r",
                  port, addr[0], addr[1], pkt.msg_type, len(pkt.body), pkt.seq, pkt.ack, body_preview)
        self.log_device(port, addr, pkt, body_preview)

        # Known camera first: a camera's 0x04 "200 OK" must not be mistaken
        # for a phone's hole-punch request.
        for cam in self.cameras.values():
            if (cam.host, cam.port) == addr:
                self.on_camera(cam, addr, pkt, port)
                return

        # Known app: after the hole punch, type 0x04 is an HTTP command,
        # not another hole request.  A repeated 0x02/0x04 with an id+password
        # body is still a hole request.
        if addr in self.sessions:
            if pkt.msg_type in (HPREQ, HPPKT) and not (
                    pkt.body.startswith(b"GET ") or pkt.body.startswith(b"POST ")):
                self.on_hole(addr, pkt, port)
            else:
                self.on_app_data(addr, pkt, port)
            return

        # App -> server discovery.
        if pkt.msg_type in (SERVERWHO, QUERYREQ2) and port == PORT_QUERY:
            self.on_query(addr, pkt)
            return
        if pkt.msg_type in (HPREQ, HPPKT) and port in (PORT_P2P, PORT_QUERY):
            self.on_hole(addr, pkt, port)
            return
        if pkt.msg_type == RLREQ and port in (PORT_RELAY, PORT_P2P):
            self.on_relay_request(addr, pkt, port)
            return
        if pkt.msg_type == TU_DEVINFO and port == PORT_STATUS:
            self.on_status(addr, pkt)
            return

        # Unknown source.  Try to learn it as a camera.
        dev_id = self.dev_id_from_http(pkt.body) or guess_dev_id(pkt.body)
        if dev_id and pkt.msg_type in (REG, REGRS, TU_DEVINFO, TU_DEVINFO2,
                                       TU_DEVINFO_R, TU_DEVINFO_R2, TU_DEVINFOS,
                                       TU_DEVINFOS_R, TU_HELLO1, TU_NATTST,
                                       TU_NATTST_R, DEVTST, TU_DEVADDR,
                                       TU_DEVADDRR):
            self.learn_camera(dev_id, addr, pkt.msg_type, port)
            # The UIV3 firmware sends /api/userip.asp as a P2P REG payload.
            # Acknowledge it so the camera keeps this NAT mapping alive.
            if b"GET /api/userip.asp" in pkt.body[:512]:
                http_ok = (b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n"
                           b"Connection: close\r\n\r\n")
                self.send(http_ok, addr, port)
                self.send(Packet(REGRS, body=fixed_str(dev_id, 20)).encode(), addr, port)
                LOG.info("HTTP registration from %s:%d dev=%s", addr[0], addr[1], dev_id)
            return

        # App packet before hole (e.g. a resend); use "username=" if present.
        if pkt.msg_type in (WEBDAT, HPPKT, HPREQ, RLREQ, RLACK):
            dev_id = self.dev_id_from_http(pkt.body) or guess_dev_id(pkt.body)
            if dev_id:
                self.sessions[addr] = Session(dev_id, addr, port)
                self.app_dev[addr] = dev_id
                LOG.info("late app session %s:%d -> %s", addr[0], addr[1], dev_id)
                self.on_app_data(addr, pkt, port)
                return

        LOG.info("unknown packet :%d %s:%d type=0x%02X len=%d body=%s",
                 port, addr[0], addr[1], pkt.msg_type, len(pkt.body), pkt.body[:64].hex())

    # -------------------------------------------------------------- handlers

    def on_query(self, addr, pkt: Packet):
        dev_id = parse_dev_id(pkt.body)
        password = parse_password(pkt.body)
        if not dev_id:
            LOG.info("query without device id from %s:%d", *addr)
            return
        self.app_dev[addr] = dev_id
        self.total_queries += 1
        self.mark_seen(dev_id, "phone")
        cam = self.cameras.get(dev_id)
        if cam:
            cam.last_seen = time.time()
        LOG.info("query from %s:%d dev=%s pwd_len=%d camera=%s",
                 addr[0], addr[1], dev_id, len(password),
                 f"{cam.host}:{cam.port}" if cam else "unknown")

        p2p = self.public_host
        relay = self.public_host
        if self.mode == "direct" and cam:
            p2p = cam.host

        # 2012 native client: 0x63, 180 bytes (two server pairs).
        new = make_server_ans(dev_id, p2p, relay, p2p, relay, new_format=True)
        self.send(Packet(QUERYANS, body=new).encode(), addr, PORT_QUERY)
        # Old 2010 client: 0x61, 100 bytes.
        old = make_server_ans(dev_id, p2p, relay, new_format=False)
        self.send(Packet(SERVERANS, body=old).encode(), addr, PORT_QUERY)

    def on_hole(self, addr, pkt: Packet, port: int):
        dev_id = parse_dev_id(pkt.body) or self.app_dev.get(addr, "")
        if not dev_id:
            LOG.info("hole request without id from %s:%d", *addr)
            return
        self.total_holes += 1
        self.mark_seen(dev_id, "phone")
        cam = self.cameras.get(dev_id)
        if cam:
            cam.last_seen = time.time()

        if self.mode == "direct" and cam:
            host, rport = cam.host, cam.port
        else:
            host, rport = self.public_ip, PORT_P2P

        # Remember the phone for the admin dashboard and, in relay mode, for
        # forwarding packets from the camera back to it.
        sess = self.sessions.get(addr) or Session(dev_id, addr, PORT_P2P)
        sess.dev_id = dev_id
        sess.server_port = PORT_P2P
        sess.camera_addr = (cam.host, cam.port) if cam else None
        sess.last_seen = time.time()
        self.sessions[addr] = sess
        self.app_dev[addr] = dev_id

        body = make_hole_success(dev_id, host, rport)
        self.send(Packet(HPSUC, body=body).encode(), addr, PORT_P2P, sess=sess)
        if self.args.text_hole:
            self.send(Packet(HPPKT, body=make_hole_text(dev_id, host, rport)).encode(), addr, PORT_P2P, sess=sess)

        LOG.info("hole %s:%d dev=%s -> %s:%d (%s)", addr[0], addr[1], dev_id,
                 host, rport, "direct" if self.mode == "direct" and cam else "relay")

        if cam and self.mode == "relay" and self.args.send_hpcmd:
            # Best-effort notification.  The exact HPCMD body is not known;
            # the original server likely sends the phone address here.  The
            # relay works without it because the camera replies to the server.
            body = fixed_str(dev_id, 20) + b"\x00" * 20
            self.send(Packet(HPCMD, body=body).encode(), (cam.host, cam.port), cam.last_port)

    def on_relay_request(self, addr, pkt: Packet, port: int):
        dev_id = parse_dev_id(pkt.body) or self.app_dev.get(addr, "")
        if not dev_id:
            LOG.info("relay request without id from %s:%d", *addr)
            return
        self.total_relays += 1
        cam = self.cameras.get(dev_id)
        sess = self.sessions.get(addr) or Session(dev_id, addr, PORT_RELAY)
        sess.dev_id = dev_id
        sess.server_port = PORT_RELAY
        sess.camera_addr = (cam.host, cam.port) if cam else None
        sess.last_seen = time.time()
        self.sessions[addr] = sess
        self.app_dev[addr] = dev_id
        body = make_hole_success(dev_id, self.public_ip, PORT_RELAY)
        self.send(Packet(RLSTR, body=body).encode(), addr, PORT_RELAY, sess=sess)
        LOG.info("relay request %s:%d dev=%s camera=%s", addr[0], addr[1], dev_id,
                 f"{cam.host}:{cam.port}" if cam else "unknown")

    def on_status(self, addr, pkt: Packet):
        dev_id = parse_dev_id(pkt.body) or self.app_dev.get(addr, "")
        # Old client reads bytes 4..7 as "login time"; < 300 means online.
        body = bytearray(10)
        body[0:min(4, len(dev_id))] = dev_id.encode("ascii", "replace")[:4]
        self.send(Packet(0xC3, body=bytes(body)).encode(), addr, PORT_STATUS)
        LOG.debug("status from %s:%d dev=%s", addr[0], addr[1], dev_id)

    def on_app_data(self, addr, pkt: Packet, port: int):
        sess = self.sessions.get(addr)
        if sess is None:
            LOG.info("data from unknown app %s:%d type=0x%02X", addr[0], addr[1], pkt.msg_type)
            return
        sess.last_seen = time.time()
        sess.last_type = pkt.msg_type
        sess.packets_in += 1
        sess.bytes_in += len(pkt.body) + 12
        cam = self.cameras.get(sess.dev_id)
        if cam is None:
            LOG.info("no camera for dev=%s (app %s:%d)", sess.dev_id, addr[0], addr[1])
            return
        sess.camera_addr = (cam.host, cam.port)
        self.send(pkt.encode(), (cam.host, cam.port), cam.last_port, cam=cam)
        LOG.debug("fwd app->camera dev=%s type=0x%02X %d bytes",
                  sess.dev_id, pkt.msg_type, len(pkt.body))

    def on_camera(self, cam: Camera, addr, pkt: Packet, port: int):
        cam.last_seen = time.time()
        cam.last_type = pkt.msg_type
        cam.last_port = port
        cam.packets_in += 1
        cam.bytes_in += len(pkt.body) + 12
        self.mark_seen(cam.dev_id, "camera", addr[0])
        if pkt.msg_type in (REG, REGRS, TU_DEVINFO, TU_DEVINFO2, TU_DEVINFO_R,
                            TU_DEVINFO_R2, TU_DEVINFOS, TU_DEVINFOS_R,
                            TU_HELLO1, TU_NATTST, TU_NATTST_R, DEVTST,
                            TU_DEVADDR, TU_DEVADDRR):
            return
        targets = [s for s in self.sessions.values() if s.dev_id == cam.dev_id]
        if not targets:
            LOG.debug("camera %s packet type=0x%02X but no app session", cam.dev_id, pkt.msg_type)
            return
        for sess in targets:
            sess.packets_out += 1
            sess.bytes_out += len(pkt.body) + 12
            self.send(pkt.encode(), sess.app_addr, sess.server_port)
            LOG.debug("fwd camera->app dev=%s type=0x%02X %d bytes",
                      cam.dev_id, pkt.msg_type, len(pkt.body))

    def mark_seen(self, dev_id: str, kind: str, host: str = "") -> None:
        """Remember the last-seen time of a device id, persisted across restarts."""
        if not dev_id or not looks_like_dev_id(dev_id):
            return  # unparseable placeholder like "GET /api/userip.asp?"
        now = time.time()
        old = self.seen.get(dev_id)
        self.seen[dev_id] = {"last": now, "host": host or (old or {}).get("host", ""),
                             "kind": kind}
        if now - self.save_seen > 60:
            self.save_seen = now
            self._write_seen()

    def _write_seen(self) -> None:
        if not self.seen_db:
            return
        self.prune_seen()
        try:
            tmp = self.seen_db + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.seen, fh)
            os.replace(tmp, self.seen_db)
        except OSError as exc:
            LOG.warning("seen db %s: %s", self.seen_db, exc)

    def prune_seen(self, max_age: float = 90 * 86400) -> None:
        cutoff = time.time() - max_age
        for dev_id in list(self.seen):
            if self.seen[dev_id]["last"] < cutoff:
                del self.seen[dev_id]

    def seen_stats(self) -> dict:
        now = time.time()
        windows = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
        out = {"cameras": {}, "phones": {}}
        for key, span in windows.items():
            out["cameras"][f"cameras_{key}"] = sum(
                1 for e in self.seen.values()
                if e["kind"] == "camera" and now - e["last"] < span)
            out["phones"][f"phones_{key}"] = sum(
                1 for e in self.seen.values()
                if e["kind"] == "phone" and now - e["last"] < span)
        return out

    def learn_camera(self, dev_id: str, addr: tuple[str, int], msg_type: int, port: int = PORT_P2P):
        now = time.time()
        if old := self.cameras.get(dev_id):
            old.last_seen = now
            old.last_type = msg_type
            old.last_port = port
            self.mark_seen(dev_id, addr[0])
            return
        self.cameras[dev_id] = Camera(dev_id, addr[0], addr[1], last_type=msg_type, last_port=port)
        self.mark_seen(dev_id, addr[0])
        LOG.info("learned camera %s at %s:%d via :%d (type=0x%02X)", dev_id, addr[0], addr[1], port, msg_type)

    @staticmethod
    def dev_id_from_http(body: bytes) -> str:
        text = body[:512].decode("ascii", "ignore")
        for key in ("username=", "DID=", "did="):
            pos = text.find(key)
            if pos >= 0:
                value = text[pos + len(key):].split("&", 1)[0].split(" ", 1)[0].strip()
                if looks_like_dev_id(value):
                    return value
        return ""

    # -------------------------------------------------------------- geoip

    def geoip_country(self, host: str) -> tuple[str, str]:
        """(ISO country code, country name) for an IPv4 address, or ("", "")."""
        geo = _GeoIP.instance(self.args.geoip_dir)
        iso = geo.lookup(host)
        return iso, getattr(geo, "names", {}).get(iso, iso)


class _GeoIP:
    """Lazy-loaded GeoLite2-Country CSV lookup; reloads when the files change."""

    def __init__(self, directory: str):
        self.directory = directory
        self.blocks: list[tuple[int, int, str]] = []  # sorted (start, end, iso)
        self.starts: list[int] = []
        self.loaded_mtime = 0.0
        self.loaded_at = 0.0

    @classmethod
    def instance(cls, directory: str) -> "_GeoIP":
        if directory not in _GEOIPS:
            _GEOIPS[directory] = cls(directory)
        geo = _GEOIPS[directory]
        geo.maybe_reload()
        return geo

    def _geo_file(self, name: str) -> str:
        """Newest 'name' (e.g. GeoLite2-Country-Blocks-IPv4.csv) under the dir."""
        from glob import glob
        hits = glob(os.path.join(self.directory, "**", name), recursive=True)
        return max(hits, key=os.path.getmtime) if hits else ""

    def maybe_reload(self) -> None:
        if not self.directory:
            return
        try:
            blocks_file = self._geo_file("GeoLite2-Country-Blocks-IPv4.csv")
            if not blocks_file:
                return
            mtime = os.path.getmtime(blocks_file)
        except OSError:
            return
        if self.blocks and mtime == self.loaded_mtime:
            return
        self._load(mtime)

    def _load(self, mtime: float) -> None:
        directory = self.directory
        try:
            self._load_csv(directory, mtime)
            LOG.info("geoip loaded %d networks from %s", len(self.blocks), directory)
        except Exception as exc:  # noqa: BLE001 - enrichment only
            LOG.warning("geoip load failed: %s", exc)

    def _load_csv(self, directory: str, mtime: float) -> None:
        locations: dict[int, str] = {}
        self.names: dict[str, str] = {}
        locations_path = self._geo_file("GeoLite2-Country-Locations-en.csv")
        if not locations_path:
            raise FileNotFoundError("GeoLite2-Country-Locations-en.csv")
        with open(locations_path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                iso = (row.get("country_iso_code") or "").strip()
                if not iso:
                    continue
                locations[row["geoname_id"]] = iso
                self.names[iso] = (row.get("country_name") or iso).strip()
        blocks_path = self._geo_file("GeoLite2-Country-Blocks-IPv4.csv")
        rows = []
        with open(blocks_path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                geoname = row.get("geoname_id") or row.get("registered_country_geoname_id") or ""
                if not geoname:
                    continue
                iso = locations.get(geoname)
                if not iso:
                    continue
                net = ipaddress.ip_network(row["network"], strict=False)
                if net.version != 4:
                    continue
                rows.append((int(net.network_address), int(net.broadcast_address), iso))
        rows.sort()
        self.blocks = rows
        self.starts = [r[0] for r in rows]
        self.loaded_mtime = mtime
        self.loaded_at = time.time()

    def lookup(self, host: str) -> str:
        if not self.blocks:
            return ""
        try:
            ip = int(ipaddress.IPv4Address(host))
        except (ValueError, ipaddress.AddressValueError):
            return ""
        index = bisect.bisect_right(self.starts, ip) - 1
        if index < 0:
            return ""
        start, end, iso = self.blocks[index]
        return iso if start <= ip <= end else ""


_GEOIPS: dict[str, _GeoIP] = {}


def _dev_label(dev_id: str, address: str) -> str:
    """Escaped label set `{dev_id="...", address="..."}` for a metric line."""
    dev = dev_id.replace("\\", "").replace('"', "")
    addr = address.replace("\\", "").replace('"', "")
    return f'dev_id="{dev}",address="{addr}"'


def _fmt_bytes(value: int) -> str:
    value = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


class AdminHandler(BaseHTTPRequestHandler):
    """Small read-only dashboard/API for the operator."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        LOG.debug("admin %s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authorized(self) -> bool:
        token = getattr(self.server, "token", "")
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        if header == f"Bearer {token}":
            return True
        query = parse_qs(urlparse(self.path).query)
        return query.get("token", [""])[0] == token

    def do_GET(self):
        path = urlparse(self.path).path
        # Public counters for usage.p2pipcam.com: no token, CORS enabled.
        if path == "/api/stats":
            stats = self.server.p2p.seen_stats()
            snap = self.server.p2p.snapshot()
            stats.update({
                "cameras_online": sum(1 for c in snap["cameras"] if c["online"]),
                "phones_active": sum(1 for s in snap["sessions"] if s["active"]),
                "uptime_seconds": snap["uptime_seconds"],
                "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            body = json.dumps(stats, indent=2).encode()
            self._send(200, body + b"\n", "application/json",
                       {"Access-Control-Allow-Origin": "*"})
            return
        # Prometheus scrape: no token required even if one is set.
        if path == "/metrics":
            self._send(200, self._metrics().encode(), "text/plain; version=0.0.4")
            return
        if not self._authorized():
            self._send(401, b'{"error":"unauthorized"}\n', "application/json")
            return
        if path == "/api/connections":
            body = json.dumps(self.server.p2p.snapshot(), indent=2).encode()
            self._send(200, body + b"\n", "application/json")
            return
        if path == "/api/health":
            snap = self.server.p2p.snapshot()
            body = json.dumps({
                "ok": True,
                "uptime_seconds": snap["uptime_seconds"],
                "cameras": len(snap["cameras"]),
                "sessions": len(snap["sessions"]),
            }).encode()
            self._send(200, body + b"\n", "application/json")
            return
        if path == "/":
            self._send(200, self._dashboard().encode(), "text/html; charset=utf-8")
            return
        self._send(404, b"not found\n", "text/plain")

    def _metrics(self) -> str:
        """Prometheus text-format exposition for Grafana."""
        snap = self.server.p2p.snapshot()
        lines = [
            "# HELP p2pipcam_uptime_seconds Server uptime in seconds.",
            "# TYPE p2pipcam_uptime_seconds gauge",
            f"p2pipcam_uptime_seconds {snap['uptime_seconds']}",
            "# HELP p2pipcam_packets_total Total UDP packets received.",
            "# TYPE p2pipcam_packets_total counter",
            f"p2pipcam_packets_total {snap['total_packets']}",
            "# HELP p2pipcam_bytes_total Total UDP bytes received.",
            "# TYPE p2pipcam_bytes_total counter",
            f"p2pipcam_bytes_total {snap['total_bytes']}",
            "# HELP p2pipcam_requests_total Total TUTK requests by kind.",
            "# TYPE p2pipcam_requests_total counter",
            f"p2pipcam_requests_total{{kind=\"query\"}} {snap['total_queries']}",
            f"p2pipcam_requests_total{{kind=\"hole\"}} {snap['total_holes']}",
            f"p2pipcam_requests_total{{kind=\"relay\"}} {snap['total_relays']}",
            "# HELP p2pipcam_devices Number of known cameras/phone sessions.",
            "# TYPE p2pipcam_devices gauge",
            f"p2pipcam_devices{{kind=\"camera\"}} {len(snap['cameras'])}",
            f"p2pipcam_devices{{kind=\"phone\"}} {len(snap['sessions'])}",
        ]
        online = sum(1 for c in snap["cameras"] if c["online"])
        active = sum(1 for s in snap["sessions"] if s["active"])
        lines += [
            f"p2pipcam_devices{{kind=\"camera_online\"}} {online}",
            f"p2pipcam_devices{{kind=\"phone_active\"}} {active}",
            "# HELP p2pipcam_device_packets Packets handled per device.",
            "# TYPE p2pipcam_device_packets gauge",
            "# HELP p2pipcam_device_bytes Bytes handled per device.",
            "# TYPE p2pipcam_device_bytes gauge",
        ]
        for cam in snap["cameras"]:
            label = _dev_label(cam["dev_id"], cam["address"])
            lines.append(
                f"p2pipcam_device_packets{{kind=\"camera\",{label}}} "
                f"{cam['packets_in'] + cam['packets_out']}")
            lines.append(
                f"p2pipcam_device_bytes{{kind=\"camera\",{label}}} "
                f"{cam['bytes_in'] + cam['bytes_out']}")
        for sess in snap["sessions"]:
            label = _dev_label(sess["dev_id"], sess["app_address"])
            lines.append(
                f"p2pipcam_device_packets{{kind=\"phone\",{label}}} "
                f"{sess['packets_in'] + sess['packets_out']}")
            lines.append(
                f"p2pipcam_device_bytes{{kind=\"phone\",{label}}} "
                f"{sess['bytes_in'] + sess['bytes_out']}")
        lines.append("# HELP p2pipcam_geoip_networks Loaded GeoLite2 networks.")
        lines.append("# TYPE p2pipcam_geoip_networks gauge")
        geo = _GEOIPS.get(getattr(self.server.p2p.args, "geoip_dir", ""))
        lines.append(f"p2pipcam_geoip_networks {len(geo.blocks) if geo else 0}")
        return "\n".join(lines) + "\n"

    def _dashboard(self) -> str:
        """Static shell; JavaScript pulls /api/connections and renders the tables."""
        return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>p2pipcam demo</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 24px; font: 14px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
          background: #0f1218; color: #d7dde8; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  h2 { font-size: 15px; margin: 28px 0 8px; color: #9fb0c8; }
  .meta { color: #8b9bb4; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #263041; white-space: nowrap; }
  th { color: #9fb0c8; font-weight: 600; }
  tr:hover td { background: #161d27; }
  .online { color: #5fd18b; }
  .stale { color: #d8a657; }
  .empty { color: #67758a; text-align: center; padding: 18px; }
  .api { color: #7fb7ff; }
</style>
</head>
<body>
<h1>p2pipcam demo</h1>
<div class="meta" id="meta">loading…</div>
<h2>Cameras</h2>
<table><thead><tr>
  <th>Device</th><th>Address</th><th>Country</th><th>Last type</th><th>Last seen</th><th>State</th>
  <th>Packets in/out</th><th>Bytes in/out</th>
</tr></thead><tbody id="cameras"></tbody></table>
<h2>Phone sessions</h2>
<table><thead><tr>
  <th>Device</th><th>Phone</th><th>Country</th><th>Relay port</th><th>Camera</th><th>Last type</th>
  <th>Last seen</th><th>State</th><th>Packets in/out</th><th>Bytes in/out</th>
</tr></thead><tbody id="sessions"></tbody></table>
<p class="api">JSON: <a href="/api/connections">/api/connections</a> · health: <a href="/api/health">/api/health</a></p>
<script>
const esc = v => String(v).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const cells = (parts, extra = '') =>
  `<tr${extra}>` + parts.map(p => `<td>${p === undefined || p === null ? '—' : p}</td>`).join('') + '</tr>';
const bytes = n => {
  n = Number(n) || 0; const u = ['B','KB','MB','GB']; let i = 0;
  while (n >= 1024 && i < u.length - 2) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n) + ' ' + u[i];
};
async function refresh() {
  try {
    const snap = await (await fetch('/api/connections')).json();
    document.getElementById('meta').innerHTML =
      `uptime ${esc(snap.uptime_seconds)}s · mode ${esc(snap.mode)} · ` +
      `public ${esc(snap.public_host)} (${esc(snap.public_ip)}) · ` +
      `${esc(snap.total_packets)} packets / ${bytes(snap.total_bytes)} · ` +
      `queries ${esc(snap.total_queries)} · holes ${esc(snap.total_holes)} · relays ${esc(snap.total_relays)}`;
    const cams = snap.cameras || [];
    document.getElementById('cameras').innerHTML = cams.length ? cams.map(c =>
      cells([esc(c.dev_id), esc(c.address),
             c.country ? esc(c.country) + ' ' + esc(c.country_name) : undefined,
             esc(c.last_type), esc(c.last_seen_seconds) + 's',
             `<span class="${c.online ? 'online' : 'stale'}">${c.online ? 'online' : 'stale'}</span>`,
             esc(c.packets_in) + ' / ' + esc(c.packets_out),
             bytes(c.bytes_in) + ' / ' + bytes(c.bytes_out)],
            c.online ? '' : ' style="opacity:.45"')).join('')
      : `<tr><td colspan="8" class="empty">no cameras yet</td></tr>`;
    const sess = snap.sessions || [];
    document.getElementById('sessions').innerHTML = sess.length ? sess.map(s =>
      cells([esc(s.dev_id), esc(s.app_address),
             s.country ? esc(s.country) + ' ' + esc(s.country_name) : undefined,
             'UDP ' + esc(s.server_port), esc(s.camera_address), esc(s.last_type),
             esc(s.last_seen_seconds) + 's',
             `<span class="${s.active ? 'online' : 'stale'}">${s.active ? 'active' : 'stale'}</span>`,
             esc(s.packets_in) + ' / ' + esc(s.packets_out),
             bytes(s.bytes_in) + ' / ' + bytes(s.bytes_out)],
            s.active ? '' : ' style="opacity:.45"')).join('')
      : `<tr><td colspan="10" class="empty">no sessions yet</td></tr>`;
  } catch (err) {
    document.getElementById('meta').textContent = 'fetch failed: ' + err;
  }
}
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bind", default="0.0.0.0", help="local address to bind")
    parser.add_argument("--public-host", default="127.0.0.1",
                        help="hostname/IP that phones and cameras can reach this server on")
    parser.add_argument("--mode", choices=("relay", "direct"), default="relay")
    parser.add_argument("--devices", help="JSON file with pinned device -> host/port mappings")
    parser.add_argument("--text-hole", action="store_true",
                        help="also send the older 0x04 '200 OK' hole packet")
    parser.add_argument("--send-hpcmd", action="store_true",
                        help="send an experimental 0x03 HPCMD notification to the camera")
    parser.add_argument("--admin-bind", default="127.0.0.1",
                        help="admin dashboard bind address (default loopback)")
    parser.add_argument("--admin-port", type=int, default=8081,
                        help="admin dashboard port; 0 disables it")
    parser.add_argument("--admin-token", default="",
                        help="optional bearer token for the admin dashboard/API")
    parser.add_argument("--device-log", default="",
                        help="append a JSON line per source packet to this file")
    parser.add_argument("--seen-db", default="",
                        help="JSON file tracking last-seen times of devices (for /api/stats)")
    parser.add_argument("--geoip-dir", default="",
                        help="directory with extracted MaxMind GeoLite2-Country CSVs")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args))


async def run(args):
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    devices = {}
    if args.devices:
        with open(args.devices, "r", encoding="utf-8") as fh:
            devices = json.load(fh)
    args.devices = devices

    server = Server(args)
    loop = asyncio.get_running_loop()
    transports = {}
    for port in (PORT_STATUS, PORT_QUERY, PORT_P2P, PORT_RELAY):
        transport, _ = await loop.create_datagram_endpoint(
            lambda port=port: P2PServer(server, port),
            local_addr=(args.bind, port),
        )
        transports[port] = transport
        server.transports[port] = transport

    LOG.info("listening on %s UDP 30000-30004", args.bind)
    LOG.info("public host: %s (%s), mode=%s", server.public_host, server.public_ip, server.mode)

    admin = None
    if args.admin_port:
        admin = ThreadingHTTPServer((args.admin_bind, args.admin_port), AdminHandler)
        admin.p2p = server
        admin.token = args.admin_token
        threading.Thread(target=admin.serve_forever, daemon=True).start()
        LOG.info("admin dashboard http://%s:%d/", args.admin_bind, args.admin_port)
        if args.admin_bind not in ("127.0.0.1", "::1", "localhost") and not args.admin_token:
            LOG.warning("admin dashboard is exposed without --admin-token")

    LOG.info("point www.p2pipcam.com at this machine; waiting for packets")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        if admin is not None:
            admin.shutdown()
        for transport in transports.values():
            transport.close()


if __name__ == "__main__":
    main()
