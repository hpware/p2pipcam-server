#!/usr/bin/env python3
"""Minimal stand-in for www.p2pipcam.com's HTTP registration API.

The camera sends this request (captured from its UDP 10139 state block):

    GET /api/userip.asp?username=ADB01271&userpwd=...&vertype=906
        &language=0&dtype=0&tcpport=21870&lanip=192.168.30.49&

The original server records the camera's public IP/port and probably returns
a short status.  This server logs the request, saves it to JSONL, and returns a
configurable body (empty by default, which is what www.nwsvr1.com returns for
an unknown camera).

Run on the public server:

    sudo python3 fake_cloud.py --port 80 --save registrations.jsonl

Then point www.p2pipcam.com at that server and power-cycle the camera.  Watch
the log and the UDP 30000-30004 traffic with p2p_server.py -v.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import BaseRequestHandler, ThreadingUDPServer, ThreadingMixIn
from urllib.parse import parse_qs, urlparse
import threading

LOG = logging.getLogger("cloud")
SENSITIVE = {"userpwd", "pwd", "password"}


def redact(params: dict[str, list[str]]) -> dict[str, str]:
    out = {}
    for key, values in params.items():
        value = values[-1] if values else ""
        if key.lower() in SENSITIVE:
            out[key] = "*" * len(value)
        else:
            out[key] = value
    return out


class UDPHandler(BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        text = data.decode("ascii", "replace")
        first = text.splitlines()[0] if text else ""
        parsed = urlparse(first.split(" ", 2)[1]) if first.startswith("GET ") else urlparse("")
        params = parse_qs(parsed.query)
        LOG.info("UDP request from %s:%d path=%s params=%s", self.client_address[0],
                 self.client_address[1], parsed.path, redact(params))
        text = self.server.response_body
        if text == "source":
            text = self.client_address[0]
        body = text.encode()
        if self.server.udp_http:
            body = (b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n" + body)
        sock.sendto(body, self.client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "p2pipcam-fake/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        msg = fmt % args
        msg = re.sub(r"(userpwd=)[^&\s]+", r"\1***", msg)
        LOG.info("%s - %s", self.address_string(), msg)

    def _handle(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        clean = redact(params)
        LOG.info("request %s from %s params=%s", parsed.path, self.client_address[0], clean)

        if parsed.path in ("/api/userip.asp", "/api/userip.asp/"):
            record = {
                "time": time.time(),
                "remote_ip": self.client_address[0],
                "path": parsed.path,
                "params": {k: (v[-1] if v else "") for k, v in params.items()},
            }
            if self.server.save_path:
                with open(self.server.save_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            text = self.server.response_body
            if text == "source":
                text = self.client_address[0]
            body = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if body:
                self.wfile.write(body)
            return

        if parsed.path == "/":
            body = b"p2pipcam demo\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._handle()

    def do_POST(self):
        # The camera uses GET, but accept POST for experimentation.
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length:
            self.rfile.read(length)
        self._handle()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--body", default="", help="response body for /api/userip.asp")
    ap.add_argument("--save", default="", help="JSONL file for registration requests")
    ap.add_argument("--no-tcp", action="store_true", help="bind only UDP (leave TCP 80 to nginx)")
    ap.add_argument("--no-udp", action="store_true", help="disable the UDP listener")
    ap.add_argument("--udp-http", action="store_true", help="wrap the UDP body in HTTP headers")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    httpd = None
    if not args.no_tcp:
        try:
            httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
        except PermissionError:
            LOG.error("TCP port %d needs root; rerun with sudo or use --port 8080", args.port)
            raise SystemExit(1)
        httpd.save_path = args.save
        httpd.response_body = args.body
        LOG.info("TCP listening on %s:%d, response body=%r", args.bind, args.port, args.body)
        if args.save:
            LOG.info("saving registrations to %s", os.path.abspath(args.save))

    udp = None
    if not args.no_udp:
        class UDPServer(ThreadingUDPServer):
            allow_reuse_address = True

        try:
            udp = UDPServer((args.bind, args.port), UDPHandler)
        except PermissionError:
            LOG.error("UDP port %d needs root; rerun with sudo", args.port)
            raise SystemExit(1)
        udp.response_body = args.body
        udp.udp_http = args.udp_http
        threading.Thread(target=udp.serve_forever, daemon=True).start()
        LOG.info("UDP listening on %s:%d (raw HTTP request expected)", args.bind, args.port)

    if httpd is not None:
        httpd.serve_forever()
    elif udp is not None:
        LOG.info("TCP listener disabled; serving UDP only")
        udp.serve_forever()
    else:
        LOG.error("both TCP and UDP listeners are disabled")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
