#!/usr/bin/env python3
"""Tiny reference client for the old P2PIPCam protocol.

Examples:

    # Ask a server where a camera is, then hole-punch it.
    python3 p2p_client.py query  --server 127.0.0.1 --dev-id ABC12345 --password 12345678

    # Send the same HTTP-over-UDP request the Android app sends.
    python3 p2p_client.py http   --server 127.0.0.1 --dev-id ABC12345 --password 12345678 \
                                 --user admin --pass admin

This is deliberately dependency-free; the iOS client needs the same four
steps: SERVERWHO -> SERVERANS, HPPKT/HPREQ -> HPSUC, then HTTP-over-UDP.
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time

from p2p_protocol import (
    HPREQ, HPSUC, PORT_P2P, PORT_QUERY, QUERYANS, SERVERANS, SERVERWHO,
    WEBDAT, Packet, http_basic_header, make_id_password, parse_hole_success,
    parse_packet, parse_server_ans,
)


def query(server: str, dev_id: str, password: str, timeout: float = 2.0) -> dict:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(Packet(SERVERWHO, body=make_id_password(dev_id, password)).encode(),
                    (server, PORT_QUERY))
        deadline = time.time() + timeout
        while time.time() < deadline:
            data, addr = sock.recvfrom(2048)
            pkt = parse_packet(data)
            if pkt and pkt.msg_type in (SERVERANS, QUERYANS):
                answer = parse_server_ans(pkt.body)
                answer["type"] = pkt.msg_type
                answer["from"] = f"{addr[0]}:{addr[1]}"
                return answer
        raise TimeoutError("no SERVERANS/QUERYANS")
    finally:
        sock.close()


def hole(server: str, dev_id: str, password: str, timeout: float = 2.0) -> dict:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(Packet(HPREQ, body=make_id_password(dev_id, password)).encode(),
                    (server, PORT_P2P))
        deadline = time.time() + timeout
        while time.time() < deadline:
            data, addr = sock.recvfrom(2048)
            pkt = parse_packet(data)
            if pkt and pkt.msg_type == HPSUC:
                answer = parse_hole_success(pkt.body)
                answer["from"] = f"{addr[0]}:{addr[1]}"
                return answer
        raise TimeoutError("no HPSUC")
    finally:
        sock.close()


def http_probe(server: str, dev_id: str, password: str,
               user: str, view_password: str, timeout: float = 4.0):
    """Send the 0x2A WEBDAT request used for the first camera command."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    try:
        auth = http_basic_header(user, view_password)
        body = (f"GET /cmmd=52&username={dev_id}& HTTP/1.1\n\n"
                f"{auth}\r\n").encode("ascii")
        sock.sendto(Packet(WEBDAT, body=body).encode(), (server, PORT_P2P))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            pkt = parse_packet(data)
            if pkt is None:
                continue
            text = pkt.body[:120].decode("ascii", "replace")
            print(f"rx type=0x{pkt.msg_type:02X} seq={pkt.seq} ack={pkt.ack} "
                  f"len={len(pkt.body)} {text!r}")
    finally:
        sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("query", "hole", "http", "all"))
    ap.add_argument("--server", required=True)
    ap.add_argument("--dev-id", required=True)
    ap.add_argument("--password", default="")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--pass", dest="view_password", default="admin")
    args = ap.parse_args()

    if args.command in ("query", "all"):
        print("query:", query(args.server, args.dev_id, args.password))
    if args.command in ("hole", "all"):
        print("hole :", hole(args.server, args.dev_id, args.password))
    if args.command in ("http", "all"):
        http_probe(args.server, args.dev_id, args.password, args.user, args.view_password)


if __name__ == "__main__":
    main()
