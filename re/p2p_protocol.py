"""Packet helpers for the old TUTK/"P2P IP Cam Lite" UDP protocol.

The wire format was recovered from:
  * com.tutk.VideoViewer (P2PIPCamLite.apk, 2010, pure Java)
  * com.tutk.mjpeg.P2PMJV3APIs + libP2PMJV3.so
    (P2PIPCamLite 1.1.0.4, 2012, native SDK)

All multi-byte header fields are big-endian.  A packet is:

    offset  size  meaning
    0       1     version (0xA0)
    1       1     message type
    2       2     payload length (big-endian, 16 bit)
    4       4     sequence number (big-endian)
    8       4     ack number (big-endian; low 24 bits are frame size for video)
    12      N     payload

The original Java code stores payload length in the low 16 bits of an int,
so the high 16 bits are always zero in practice.
"""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass

VERSION = 0xA0
HEADER = struct.Struct(">BBHII")
HEADER_SIZE = HEADER.size

# Message types, from com.tutk.VideoViewer.Protocol and the native library.
REG = 0x01
HPREQ = 0x02          # app -> server: please open a hole
HPCMD = 0x03          # server -> camera: connect to this peer
HPPKT = 0x04          # camera -> app / app -> camera: "200 OK" hole packet
HPFAI = 0x05          # hole punch failed
HPSUC = 0x06          # server -> app: hole punch succeeded, here is the peer
REGRS = 0x07

SERVERWHO = 0x60      # 96  old app -> server: where is this device?
QUERYREQ2 = 0x62      # 98  2012 native client -> server: same query
SERVERANS = 0x61      # 97  server -> app: 20-byte id + 40-byte p2p + 40-byte relay
QUERYANS = 0x63       # 99  newer server -> app: 20 + 4 * 40 bytes (2 p2p + 2 relay)

IMGMID = 0x22         # 34
IMGSTR = 0x21         # 33
IMGEND = 0x23         # 35
AUTREQ = 0x25         # 37
AUTMID = 0x26         # 38
WEBDAT = 0x2A         # 42  HTTP request/response carried over UDP
ACK = 0x2C            # 44

RLREQ = 0x64          # 100
RLACK = 0x65          # 101
RLSTR = 0x66          # 102
RLEND = 0x67          # 103

TU_DEVINFO = 0xC0
TU_DEVINFO_R = 0xC1
TU_DEVINFOS = 0xC2
TU_DEVINFOS_R = 0xC3
TU_DEVINFO2 = 0xCA
TU_DEVINFO_R2 = 0xCB
TU_DEVADDR = 0xC8
TU_DEVADDRR = 0xC9
TU_HELLO1 = 0xD1
TU_NATTST = 0xC4
TU_NATTST_R = 0xC5
DEVTST = 0xC6

# Ports used by both the 2010 app and the 2012 native library.
PORT_STATUS = 30000
PORT_QUERY = 30002
PORT_P2P = 30003
PORT_RELAY = 30004
PORT_CLIENT = 6000


@dataclass
class Packet:
    msg_type: int
    seq: int = 0
    ack: int = 0
    body: bytes = b""

    def encode(self) -> bytes:
        if len(self.body) > 0xFFFF:
            raise ValueError("payload too large for this protocol")
        return HEADER.pack(VERSION, self.msg_type, len(self.body), self.seq, self.ack) + self.body

    @property
    def frame_size(self) -> int:
        return self.ack & 0xFFFFFF


def parse_packet(data: bytes) -> Packet | None:
    """Parse a datagram.  Returns None for malformed/short packets."""
    if len(data) < HEADER_SIZE:
        return None
    ver, msg_type, length, seq, ack = HEADER.unpack_from(data)
    if ver not in (0x00, VERSION):
        return None
    if length > len(data) - HEADER_SIZE:
        # Some devices put junk in the high half; trust the datagram length.
        length = len(data) - HEADER_SIZE
    return Packet(msg_type, seq, ack, data[HEADER_SIZE:HEADER_SIZE + length])


def cstr(raw: bytes, limit: int | None = None) -> str:
    raw = raw[:limit] if limit else raw
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace")


def fixed_str(value: str, size: int) -> bytes:
    raw = value.encode("ascii", "replace")[:size]
    return raw + b"\x00" * (size - len(raw))


def parse_dev_id(body: bytes) -> str:
    """The first 20 bytes are the device id; vendors usually use 8 chars."""
    return cstr(body[:20]).strip()


def parse_password(body: bytes) -> str:
    return cstr(body[20:40]).strip()


def make_id_password(dev_id: str, password: str, size: int = 40) -> bytes:
    body = fixed_str(dev_id, 20) + fixed_str(password, 20)
    return body[:size].ljust(size, b"\x00")


def parse_server_ans(body: bytes) -> dict[str, str]:
    """Parse 0x61 (100 bytes) or 0x63 (180 bytes)."""
    out = {
        "dev_id": cstr(body[:20]),
        "p2p1": cstr(body[20:60]),
        "relay1": cstr(body[60:100]),
    }
    if len(body) >= 180:
        out["p2p2"] = cstr(body[100:140])
        out["relay2"] = cstr(body[140:180])
    return out


def make_server_ans(dev_id: str, p2p1: str, relay1: str,
                    p2p2: str = "", relay2: str = "",
                    new_format: bool = True) -> bytes:
    body = fixed_str(dev_id, 20)
    body += fixed_str(p2p1, 40)
    body += fixed_str(relay1, 40)
    if new_format:
        body += fixed_str(p2p2 or p2p1, 40)
        body += fixed_str(relay2 or relay1, 40)
    return body


def ip4_to_le(ip: str) -> bytes:
    return ipaddress.IPv4Address(ip).packed[::-1]


def le_to_ip4(raw: bytes) -> str:
    return str(ipaddress.IPv4Address(raw[::-1]))


def make_hole_success(dev_id: str, ip: str, port: int) -> bytes:
    """0x06 body: 20-byte id, 4-byte little-endian IP, 2-byte little-endian port."""
    body = fixed_str(dev_id, 20)
    body += ip4_to_le(ip)
    body += struct.pack("<H", port & 0xFFFF)
    return body.ljust(40, b"\x00")


def parse_hole_success(body: bytes) -> dict[str, object]:
    if len(body) < 26:
        return {}
    return {
        "dev_id": cstr(body[:20]),
        "ip": le_to_ip4(body[20:24]),
        "port": struct.unpack("<H", body[24:26])[0],
    }


def make_hole_text(dev_id: str, ip: str, port: int) -> bytes:
    """0x04 body used by the 2010 client; it learns the peer from the UDP source."""
    return f"200 OK {dev_id} {ip}:{port}\r\n".encode("ascii")


def looks_like_dev_id(text: str) -> bool:
    if not 4 <= len(text) <= 20:
        return False
    # Real IDs are alphanumeric (sometimes with - or _).  Do not accept
    # spaces, slashes, dots, or an HTTP request line as a device ID.
    return all(c.isalnum() or c in "-_" for c in text)


def guess_dev_id(body: bytes) -> str:
    """Find a plausible device id in a registration-style payload."""
    for width in (20, 16, 12, 8):
        candidate = cstr(body[:width]).strip()
        if looks_like_dev_id(candidate):
            return candidate
    # Some payloads are text: DID=..., devid=..., UID=...
    text = body[:128].decode("ascii", "ignore")
    for key in ("DID=", "did=", "DEVID=", "devid=", "UID=", "uid="):
        pos = text.find(key)
        if pos >= 0:
            value = text[pos + len(key):].split("&", 1)[0].split("\x00", 1)[0].strip()
            if looks_like_dev_id(value):
                return value
    return ""


def http_basic_header(user: str, password: str) -> str:
    import base64
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Authorization: Basic {token}\r\n"
