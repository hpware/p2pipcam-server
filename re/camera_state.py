#!/usr/bin/env python3
"""Read the EasyN/UIV3 state block from a camera with a UDP "uicam query".

This is completely read-only: it sends the same discovery datagram the vendor's
search tool sends, then prints the returned state and the camera's cloud
registration request.

    python3 camera_state.py 192.0.2.145
"""

from __future__ import annotations

import argparse
import re
import socket
import struct
import time


def query(ip: str, port: int = 10139, timeout: float = 2.0) -> bytes:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)
    sock.bind(("", 0))
    sock.sendto(b"uicam query\x00", (ip, port))
    try:
        data, addr = sock.recvfrom(2048)
        print(f"reply from {addr[0]}:{addr[1]} ({len(data)} bytes)")
        return data
    finally:
        sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ip")
    ap.add_argument("--port", type=int, default=10139)
    args = ap.parse_args()

    data = query(args.ip, args.port)
    if len(data) < 0x38:
        print("short reply:", data.hex())
        return

    print("magic      :", data[0:4])
    print("version    :", f"{data[4]:02x}.{data[5]:02x}")
    print("mac        :", ":".join(f"{b:02x}" for b in data[8:14]))
    http_port = struct.unpack(">H", data[14:16])[0]
    print("http port  :", http_port)
    print("ip         :", socket.inet_ntoa(data[16:20]))
    print("gateway    :", socket.inet_ntoa(data[20:24]))
    print("netmask    :", socket.inet_ntoa(data[24:28]))
    print("dns1       :", socket.inet_ntoa(data[28:32]))
    print("dns2       :", socket.inet_ntoa(data[32:36]))

    print("\nASCII strings in the state block:")
    for match in re.finditer(rb"[ -~]{4,}", data):
        print(f"  {match.start():04x}  {match.group().decode('ascii', 'replace')}")

    print("\nThe cloud request is the camera's own registration data; the")
    print("username/password are the same 8-character values used by the app.")


if __name__ == "__main__":
    main()
