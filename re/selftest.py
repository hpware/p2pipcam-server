#!/usr/bin/env python3
"""End-to-end test of the relay server with a fake camera.

Run from this directory:  python3 selftest.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

from p2p_protocol import (
    PORT_P2P, PORT_QUERY, QUERYREQ2, REG, WEBDAT, Packet, fixed_str, parse_packet,
)
from p2p_client import hole, query

HERE = os.path.dirname(os.path.abspath(__file__))
DEV = "TEST1234"
PWD = "87654321"


def main():
    log = open("/tmp/p2p_selftest.log", "w")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "p2p_server.py"),
         "--bind", "127.0.0.1", "--public-host", "127.0.0.1",
         "--admin-port", "18081", "--verbose"],
        cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(1.0)
        cam = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        cam.bind(("127.0.0.1", 0))
        cam.settimeout(3.0)
        cam.sendto(Packet(REG, body=fixed_str(DEV, 20) + fixed_str(PWD, 20)).encode(),
                   ("127.0.0.1", PORT_P2P))
        time.sleep(0.3)

        answer = query("127.0.0.1", DEV, PWD)
        print("query:", answer)
        assert answer["p2p1"] == "127.0.0.1", answer
        assert answer["relay1"] == "127.0.0.1", answer

        # The 2012 native client uses 0x62 instead of 0x60.
        q = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        q.settimeout(2.0)
        q.sendto(Packet(QUERYREQ2, body=fixed_str(DEV, 20) + fixed_str(PWD, 20)).encode(),
                 ("127.0.0.1", PORT_QUERY))
        while True:
            data, _ = q.recvfrom(2048)
            pkt = parse_packet(data)
            if pkt and pkt.msg_type == 0x63:
                print("query2: type=0x63 len=%d" % len(pkt.body))
                break
        q.close()

        answer = hole("127.0.0.1", DEV, PWD)
        print("hole :", answer)
        assert answer["ip"] == "127.0.0.1", answer
        assert answer["port"] == PORT_P2P, answer

        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.bind(("127.0.0.1", 0))
        client.settimeout(3.0)
        req = (f"GET /cmmd=52&username={DEV}& HTTP/1.1\r\n"
               "Authorization: Basic YWRtaW46YWRtaW4=\r\n\r\n").encode()
        client.sendto(Packet(WEBDAT, seq=1, body=req).encode(),
                      ("127.0.0.1", PORT_P2P))

        while True:
            data, addr = cam.recvfrom(4096)
            pkt = parse_packet(data)
            print("camera got:", pkt.msg_type, pkt.body[:60])
            if pkt and pkt.msg_type == WEBDAT and b"cmmd=52" in pkt.body:
                break

        cam.sendto(Packet(WEBDAT, seq=1, body=b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n").encode(),
                   addr)
        data, addr = client.recvfrom(4096)
        pkt = parse_packet(data)
        print("client got:", pkt.msg_type, pkt.body[:40])
        assert pkt and pkt.msg_type == WEBDAT and b"200 OK" in pkt.body

        # A camera's 0x04 "200 OK" is a data packet, not a hole request.
        cam.sendto(Packet(4, body=b"200 OK " + DEV.encode() + b"\r\n").encode(), addr)
        data, _ = client.recvfrom(4096)
        pkt = parse_packet(data)
        print("client got hole packet:", pkt.msg_type, pkt.body[:40])
        assert pkt and pkt.msg_type == 4 and b"200 OK" in pkt.body

        with urllib.request.urlopen("http://127.0.0.1:18081/api/connections", timeout=3) as resp:
            snap = json.load(resp)
        print("admin:", len(snap["cameras"]), "cameras,", len(snap["sessions"]), "sessions")
        assert any(c["dev_id"] == DEV for c in snap["cameras"]), snap
        assert any(s["dev_id"] == DEV for s in snap["sessions"]), snap
        print("SELFTEST OK")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        log.close()


if __name__ == "__main__":
    main()
