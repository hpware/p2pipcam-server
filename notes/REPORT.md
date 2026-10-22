# IBT-200CP / “P2P IP Cam Lite” reverse engineering notes

Date: 2026-10-22  
Target: **I-Bt 比特數位 IBT-200CP 雲端行動眼** (IPCAM + PLC), sold in Taiwan around 2012.  
Domain in scope: `p2pipcam.com` / `www.p2pipcam.com` (the user owns it now).

---

## 0. TL;DR

* The camera is a **TUTK / ThroughTek P2P** camera sold by **I-Bt 比特數位** as the IBT-200CP. The exact OEM is not proven, but the same software family later appears as Hsmartlink (HSL) / Digma DiVision cameras.
* The camera and the phone talk to a rendezvous service at:

  | Purpose | Host | Port |
  |---|---|---|
  | Device status / login time | `www.p2pipcam.com` | UDP 30000 |
  | “Where is this camera?” query | `www.p2pipcam.com` | UDP 30002 |
  | Hole punching / P2P session | `www.p2pipcam.com` | UDP 30003 |
  | Relay fallback | `www.p2pipcam.com` | UDP 30004 |

  The old Android client binds **UDP 6000** locally.
* The correct Android app is **P2P IP Cam Lite**, package `com.tutk.VideoViewer`. Two versions were recovered:
  * 1.0 (2010/2011, pure-Java protocol) — `apk/P2PIPCamLite.apk`
  * 1.1.0.4 (2012, native `libP2PMJV3.so`) — `apk/P2PIPCamLite_1.1.0.4.apk`
* The 2012 app still hardcodes `www.p2pipcam.com` in `AppConst.AC_STR_MASTER1`.
* The camera’s web/phone UI uses HTTP-over-UDP:
  * `GET /cmmd=52&username=<device-id>& HTTP/1.1`
  * `GET /image.jpg HTTP/1.1` (multipart JPEG stream)
  * `Authorization: Basic base64(view-user:view-password)`
* A live unit was found at **192.0.2.145:21870**, MAC `02:60:6e:51:55:6e`, ID `ABC54321`. Its HTTP banner is `vid=tutkp2p ... pksize=0900 uiv3.4`; its UDP 10139 state block contains the exact cloud registration request to `www.p2pipcam.com/api/userip.asp`.
* The camera is from the **EasyN / Foscam / UIV3 / Hi3510** white-label family. The same web UI (`viwx.html`, `pant.html`, `cmmd`, `uicam query`) is documented for the EasyN FS-618A. This gives us a similar firmware/API family even though the exact IBT-200CP image is still not public.
* A working **UDP rendezvous + relay server** is in `re/p2p_server.py`; it passes an end-to-end self-test (`re/selftest.py`). A fake cloud API is in `re/fake_cloud.py`.
* **No public IBT-200CP firmware was found.** A 2019 firmware for the later **Digma DiVision 101 (I9812)** was recovered and unpacked (`firmware/`), but it is a different generation (Tuya + TUTK PPCS, Realtek RTS3903) and is **not** flashable to the IBT-200CP. It is still useful as evidence of the vendor’s later architecture.
* The old iOS app “P2P IP Cam Lite” is gone from the App Store. No IPA was obtainable without an Apple ID/device. The iOS client does not need the old binary: the wire protocol below is enough to write a new client, and the Android 2010/2012 apps are the reference implementation.

---

## 1. How the target was identified

The user supplied the model name **IBT-200P / IBT-200CP “雲端行動眼” (PLC+IPCam)**.

The archived Taiwanese product page is:

* `https://web.archive.org/web/20120413195007/http://www.i-bt.com.tw/IBT-200CP.html`
* Local copy: `web/ibt/IBT-200CP.html` (Big5; decoded text below)

Relevant text from the page:

> 產品名稱：IBT-200CP 雲端行動眼 (雙包裝) (IPCAM+PLC)  
> 手機APP、Market 免費下載「P2P IpCam Lite」即可行動監控  
> 手機連至 iPhone App Store 或 Android Market Place 並搜尋 **P2P IP Cam_Lite**  
> 攝影機外觀 帳號 (SerialNo **8碼**) 與密碼 (Password **8碼**)  
> 遠端 IE 瀏覽器：`www.allmycloud.com`

This ties the hardware to the app `P2P IP Cam Lite` and to the `p2pipcam.com` / `allmycloud.com` service.

The 2011 `p2pipcam.com` site was an “All My Cloud” login page. Its Android download directory contained exactly one APK:

```
http://www.p2pipcam.com/3g/download/Android/P2PIPCamLite.apk
```

That is the 2010 APK recovered here.

### 1.1 Company / OEM lineage

* App namespace: `com.tutk.*` → **TUTK / ThroughTek**, the P2P SDK vendor.
* 2012 app native library: `libP2PMJV3.so` (`P2PMJV3APIs`) — TUTK’s P2P/MJPEG SDK.
* Later Digma firmware contains `../../Produce/Tutk.cpp`, `PPCS_*`, `cm.kalay.net.cn`, `gm.iotcplatform.com` — the same vendor family, later generation.
* I-Bt is the Taiwanese brand. Later cameras in the same software family are sold as Hsmartlink (HSL) and Digma DiVision; the IBT-200CP itself is from 2012 and may use an earlier OEM module.

---

## 2. Recovered apps and files

All hashes are SHA-256.

| File | What it is | SHA-256 |
|---|---|---|
| `apk/P2PIPCamLite.apk` | 2010/2011 Android app, `com.tutk.VideoViewer`, v1.0, 65 KB | `63bf9c8e…d387` |
| `apk/P2PIPCamLite_1.1.0.4.apk` | 2012 Android app, v1.1.0.4, native `libP2PMJV3.so`, 541 KB | `ccaa6fc6…e778` |
| `apk/NetCam_9.1.3.7.apk` | Later HSL “NetCam” app (`hsl.p2pipcam`), 19 MB | `53d63ee9…d82d5f` |
| `apk/DigmaSmartCam_4.0.apk` | Digma SmartCam (`digma.p2pipcam`), 2017, 9.8 MB | `5587eb32…369275` |
| `apk/IPCamMobile.jar` | 2010 Symbian client (same protocol) | — |
| `apk/IP_CAM_Mobile_1010_US.jar` | 2010 Symbian client, older build | — |
| `firmware/DiVision_101_v5.1.18_update.zip` | Digma DiVision 101 (I9812) OTA, 2019, 7.2 MB | `fa655f9f…7f0a6` |
| `firmware/linux.bin` | Unpacked OTA image (uImage + SquashFS) | `ed419db9…2cf7a6` |

Decompiled sources are in `apk/jadx_lite/`, `apk/jadx_lite2/`, `apk/jadx_netcam/`, `apk/jadx_digma/`.

Useful commands:

```sh
jadx -d apk/jadx_lite2 apk/P2PIPCamLite_1.1.0.4.apk
r2 -2 -q -c 's sym.p2pmj_QueryDevice; af; pdf' apk/libs2/lib/armeabi/libP2PMJV3.so
```

---

## 2.5 Live camera findings (2026-10-22)

The user's IBT-200CP is on the LAN at:

```
IP:   192.0.2.145
Port: 21870 (HTTP)
MAC:  02:60:6e:51:55:6e  (Davicom OUI)
ID:   ABC54321           (8 characters)
```

The password is the other 8-character value on the label. It is stored locally
in `re/camera.local.json` and omitted from this report.

### 2.5.1 HTTP banner

```
$ curl -i http://192.0.2.145:21870/image.jpg
HTTP/1.1 200 OK
Content-Type: image/jpeg
Server: vid=tutkp2p       username=ABC54321&userpwd=********         alarm=11111111 status=00000000 pksize=0900 uiv3.4
```

This is the same style of banner as the EasyN FS-618A (`vid=smarteye ...
pksize=0900 uiv3.4`), with the P2P backend replaced by TUTK (`tutkp2p`).
The web UI is the old `uiv3` frameset:

```
/           -> frameset (ctrl.html, pant.html, viwx.html)
/viwx.html  -> <img src="image.jpg?..."> live JPEG display
/pant.html  -> cmmd=... controls (brightness, quality, pan/tilt)
/image.jpg  -> 320x240 JPEG snapshot
```

The `cmmd` command set matches the EasyN/Foscam table documented at
<https://wiki.london.hackspace.org.uk/view/Webcams/EasyN_FS-618A-M136>
(`2001/2003/2005` brightness, `1801/1802` resolution, `1C02` reboot, `28`
state, etc.). Do not send write commands unless you intend to change the
camera; this project only needs read-only state.

### 2.5.2 UDP state and cloud registration

A read-only discovery datagram works on **UDP 10139**:

```
uicam query\0
```

It returns 520 bytes starting with `uiv2`. `re/camera_state.py` parses it:

```
magic      : b'uiv2'
version    : 32.00
mac        : 02:60:6e:51:55:6e
http port  : 21870
ip         : 192.0.2.145
gateway    : 172.19.212.1
netmask    : 255.255.255.0
dns1       : 172.19.212.1
dns2       : 8.8.8.8
```

The tail of the same 520-byte block contains the camera's own cloud requests:

```
www.3322.org
www.p2pipcam.com
GET /dyndns/update?system=dyndns&hostname=&wildcard=OFF HTTP/1.1
Host: members.3322.org
Authorization: Basic Og==
User-Agent: myclient/1.0 me@null.net

GET /api/userip.asp?username=ABC54321&userpwd=********&vertype=906&language=0&dtype=0&tcpport=21870&lanip=192.168.30.49&
```

Key points:

* `www.p2pipcam.com` is used for **camera registration**, not just the P2P
  query. The path is `/api/userip.asp`.
* `vertype=906` is the camera's protocol/version selector.
* `tcpport=21870` is the local HTTP port.
* `lanip=192.168.30.49` is a second interface address (probably the Wi-Fi
  interface); the Ethernet address is 192.0.2.145.
* `www.3322.org` / `members.3322.org` is a public DDNS service; the camera
  tries to update an empty hostname. It is not the P2P server.

The same `/api/userip.asp` pattern appears in the Faleemi FSC880 research
(`www.nwsvr1.com/api/userip.asp`), so this is an OEM-wide cloud API, not an
IBT-specific one.

### 2.5.3 Deployed-server finding: the HTTP request arrives as a P2P REG

The deployed server exposed a useful failure: its camera list contained a bogus
device called `GET /api/userip.asp?` at the camera's public address. The camera
does not only send `/api/userip.asp` to UDP 80; it also sends the same HTTP
request as a **P2P `REG` payload to UDP 30003**. The server was treating the
first 20 bytes of the HTTP request as the device ID.

The fixed server:

* parses `username=` out of the request body instead of using the first 20 bytes;
* rejects IDs containing spaces/slashes, so `GET /api/userip.asp?` cannot be
  learned as a camera;
* replies to the camera with `HTTP/1.1 200 OK` and a P2P `REGRS` (type 7) so the
  camera keeps its UDP NAT mapping.

Local end-to-end test after the fix:

```text
learned camera ABC54321 at 127.0.0.1:52775 via :30003 (type=0x01)
HTTP registration from 127.0.0.1:52775 dev=ABC54321
query -> ABC54321
hole  -> 127.0.0.1:30003
app -> camera -> app relay OK
admin cameras [('ABC54321', '127.0.0.1:52775')]
```

The public deployment needs `p2p_protocol.py` and `p2p_server.py` updated and
restarted before the real camera will appear as `ABC54321`.

### 2.5.4 What this means

The camera-side registration is now known well enough to test without firmware:

1. Run `re/fake_cloud.py` on the public server (`www.p2pipcam.com`).
2. Point `www.p2pipcam.com` at that server.
3. Power-cycle the camera (or wait for its next registration).
4. The fake cloud logs `username`, `tcpport`, `lanip`, and the public source IP.
5. Run `re/p2p_server.py` on the same host for UDP 30000–30004 and watch the
   camera's P2P registration packets.

The exact HTTP response body is still unknown. `www.nwsvr1.com` returns an
empty `200 OK` for an unknown camera, so an empty `200` is the first thing to
try. `re/fake_cloud.py --body ok` and `--body <public-ip>` are quick variants.

---

## 3. The old protocol (verified from the 2010 Java app)

### 3.1 Packet format

Every UDP message starts with a 12-byte header (big-endian):

```
0   1  version        0xA0
1   1  message type
2   2  payload length (16-bit, big-endian)
4   4  sequence number
8   4  ack number      (low 24 bits = frame size for video)
12  N  payload
```

Source: `apk/jadx_lite/sources/com/tutk/VideoViewer/Packet.java`

The 2012 native library uses the same header. In `libP2PMJV3.so` the builder is
`pri_AssemblePacket` at `0x3c08`: it writes `0xA0`, the type, and the 16-bit
length at offsets 0, 1, 2, then calls `pri_BigEndian`.

### 3.2 Ports

From `Protocol.java`:

```java
CLIENT_PORT                    = 6000;
MAIN_P2PSERV                   = "www.p2pipcam.com";
MAIN_P2PSERV_P2PPORT           = 30003;
MAIN_P2PSERV_QUERYLOCAL_PORT   = 30002;
MAIN_P2PSERV_QUERYTSTINFO_PORT = 30000;
MAIN_RELAYSERV_PORT            = 30004;
```

### 3.3 Device query — “where is camera X?”

Old (2010) client, `GetLocalServ.java`:

1. Bind UDP `6000`.
2. Send type `0x60` (`MSGTYPE_SERVERWHO`) to `www.p2pipcam.com:30002`.
3. Body is exactly 40 bytes:
   * bytes 0–19: device ID (ASCII, null padded; real IDs are 8 chars)
   * bytes 20–39: device password (ASCII, null padded)
4. Server replies with type `0x61` (`MSGTYPE_SERVERANS`), 100-byte body:
   * 0–19: device ID
   * 20–59: P2P server hostname/IP (40 bytes)
   * 60–99: relay server hostname/IP (40 bytes)

The 2012 native library uses the **same body** but a different request type: **`0x62`** (not `0x60`). It expects a newer answer:

* Type `0x63`, **180-byte** body:
  * 0–19: device ID
  * 20–59: P2P server #1
  * 60–99: relay server #1
  * 100–139: P2P server #2
  * 140–179: relay server #2

Evidence: `p2pmj_QueryDevice` at `0x6364` checks `body[1] == 0x63` and
`payload > 0xB3`, then copies four 40-byte strings from body offsets
20/60/100/140. The field order is inferred from the four `pri_strcpySafe`
calls at `0x65a0`–`0x65d8` and the Java argument order
(`QueryDevice(id, pwd, p2p1, p2p2, relay1, relay2, timeout)`).

Our server sends **both** `0x61` and `0x63`; each client ignores the wrong one.

### 3.4 Hole punching

Old client (`ThreadP2PSession.hole2Server()`):

* Send type `0x04` (`MSGTYPE_HPPKT`) to the P2P server on **30003**.
* Body: the same 40-byte ID + password.
* Server replies with type `0x06` (`MSGTYPE_HPSUC`), 40 bytes:
  * 0–19: device ID
  * 20–23: device public IPv4, **little-endian** (e.g. `C0 A8 01 0A` for 192.168.1.10)
  * 24–25: device public UDP port, **little-endian**
  * 26–39: zero

`parseHPSUC()` then sends an HTTP GET directly to that IP/port:

```
GET /cmmd=52&username=<device-id>& HTTP/1.1

Authorization: Basic <base64(view-user:view-password)>
```

The 2012 library instead sends type `0x02` (`MSGTYPE_HPREQ`) to **30003**,
twice, and accepts `0x06` or a `0x04` “200 OK” reply. The parsing code is
`pri_DoRepeatHole` at `0x4df0` and `pri_RepeatP2PHole` at `0x4f90`.

### 3.5 Relay fallback

If hole punching fails, the client uses the relay server on **30004**:

* `0x64` (`MSGTYPE_RLREQ`) — request relay; body = ID + password.
* `0x66` (`MSGTYPE_RLSTR`) — relay assigned; body has ID at 0–19, relay IP
  little-endian at 20–23, relay port little-endian at 24–25.
* `0x65` (`MSGTYPE_RLACK`) — client acknowledges.
* `0x67` (`MSGTYPE_RLEND`) — end relay.

### 3.6 HTTP-over-UDP / video

Once the peer address is known, the client sends ordinary HTTP requests as
UDP payloads with message type `0x2A` (`MSGTYPE_WEBDAT`):

```
GET /image.jpg HTTP/1.1
Connection: Keep-Alive
Authorization: Basic <base64(view-user:view-password)>

yourip: <device-ip>:<device-port>
```

The camera replies with:

* `0x21` `MSGTYPE_IMGSTR` — first packet of a JPEG frame
* `0x22` `MSGTYPE_IMGMID` — middle packets
* `0x23` `MSGTYPE_IMGEND` — last packet
* `0x2C` `MSGTYPE_ACK` — selective ACK / retransmit request
* `0x2A` `MSGTYPE_WEBDAT` — HTTP text (401, 200, etc.)
* `0x25` `MSGTYPE_AUTREQ` — authentication request / bad password

Frame reassembly (from `Packet.java` and `ThreadP2PSession.java`):

* Application payload is split into 900-byte chunks (`PKSIZE = 900`).
* The first 230 bytes of the JPEG/HTTP stream are header (`HTTPHEAD_SIZE = 230`).
* `ackNo & 0xFFFFFF` is the total frame size.
* Packet index = `(seqNo - 230) / 900`.
* Lost packets are requested with type `0x2C` and the missing sequence numbers.
* The client buffers up to 80 packets (`80 * 900 = 72000`), max frame 81920.

Stop:

```
GET /stop HTTP/1.1
```

### 3.7 Status check

`isDevOffline()` sends type `0xC0` (`MSGTYPE_TU_DEVINFO`) to port **30000**
with the 40-byte ID/password body. A reply of type `0xC3` (`MSGTYPE_TU_DEVINFOS_R`)
has a login time at bytes 4–7; a value >= 300 means the camera is offline.

---

## 4. The 2012 app and native library

The 2012 app is the one that matches the 2012 IBT-200CP product page.

* Package: `com.tutk.VideoViewer`
* `AppConst.AC_STR_MASTER1 = "www.p2pipcam.com"`
* `Camera.init()` calls `P2PMJV3APIs.p2pmj_Initialize("www.p2pipcam.com", null, 2)`
* `lib/armeabi/libP2PMJV3.so` — 50 KB, ARM EABI5, stripped
* `P2PMJV3APIs.p2pmj_QueryDevice(...)` returns four strings:
  `p2p1`, `p2p2`, `relay1`, `relay2`
* `p2pmj_ConnectDevice(...)` takes those four strings, then authenticates with
  `p2pmj_Authorize()` and starts video.

Useful native addresses (base 0):

| Function | Address | What it shows |
|---|---|---|
| `pri_AssemblePacket` | `0x3c08` | 12-byte header, 0xA0, type, 16-bit length |
| `p2pmj_QueryDevice` | `0x6364` | type `0x62` to port `0x7532` (30002), type `0x63` answer |
| `pri_DoRepeatHole` | `0x4df0` | types 4/5/6, “200 OK”, ID check |
| `pri_RepeatP2PHole` | `0x4f90` | type `2` to port `0x7533` (30003) |
| `p2pmj_ConnectDevice` | `0x6e04` | P2P then relay selection |
| `pri_DoRelay` / `pri_Relay` | `0x5180` / `0x5238` | relay types |

Strings in `libP2PMJV3.so` confirm the HTTP commands:

```
uicam query
GET /cmmd=52&username=%s& HTTP/1.1
Authorization: Basic %s
GET /image.jpg
GET /stop.htm HTTP/1.1
GET /cmmd=25.cgi
GET /cmmd=2A.cgi
GET /cmmd=34
```

---

## 5. Later apps / later firmware (not the IBT-200CP)

These are useful for understanding where the vendor went, **not** for the
IBT-200CP.

### 5.1 `NetCam 9.1.3.7` (`hsl.p2pipcam`)

* 19 MB, 2016-era, uses `com.ndt.ppcs_api.NDT_API` (TUTK PPCS) and
  `com.p2p.pppp_api` (TUTK PPPP).
* Initialization string is empty; the native SDK uses its built-in PPCS
  defaults.
* Still contains `hsl.p2pipcam.bean.ContantsModel.SERVER`, an obfuscated
  TUTK init string.
* Push via Taobao Agoo; not relevant to the old UDP server.

### 5.2 `Digma SmartCam 4.0` (`digma.p2pipcam`)

* 2017 app for Digma DiVision (I9812).
* `DeviceManager.initialize("AACCEIAFKDMNHGNPFGCDBBENDMJGHENOCAAKFDMFNJMOPLEDIEOCBPJCDPBJPKMAEJELPHCPNICFICINJIN")`
  → TUTK `PPPP_Initialize` init string.
* Uses `libNDT_API_PPCS.so`, `libPPPP_API.so`, `libipc.so`, `libnas.so`.
* Firmware update endpoint: `http://update.digma.ru/index.php?`
  with `model=DiVision`, `product=DIGMA SmartCam`, `device=I9812`,
  `fingerprint=<sys_ver>`, `imei=<dev-id>`, `mac=<wifi-mac>`, etc.
* The update server returns `MANUAL_UPDATE` unless the device fingerprint is a
  known DiVision build. Hardcoded test file:
  `http://dbg.vip120.com/app/v1.0/e2.9.1.17.9.bin` (host is dead now; the
  origin `vip120.com` still answers 404).

### 5.3 Digma DiVision 101 firmware (2019)

Recovered from `https://ddxinfo.ru/704`:

* `DiVision_101_v5.1.18_update.zip` → `linux.bin` (7,200,292 bytes)
* Custom 0x120-byte header, then a MIPS uImage (`linux_3.10`), then a
  SquashFS 4.0 rootfs at `0x1B85EC`.
* Rootfs version `v2.3.7`, built `2019-03-04`, Realtek RTS3903.
* Main binary `usr/bin/dgiot` is a **Tuya IPC SDK + TUTK PPCS** build:
  * `../../Produce/Tutk.cpp`
  * `PPCS_Initialize`, `PPCS_Listen`, `PPCS_Read`, ...
  * `cm.kalay.net.cn`, `gm.iotcplatform.com`
  * `m2.tuyacn.com`, `m2.tuyaeu.com`, `m2.tuyaus.com`
  * `https://a2.tuyacn.com/d.json`, etc.
* This firmware does **not** contain `p2pipcam.com`. It is a completely
  different cloud generation.

Extraction commands:

```sh
unzip firmware/DiVision_101_v5.1.18_update.zip
binwalk firmware/linux.bin
dd if=firmware/linux.bin of=firmware/rootfs.sqsh bs=1 skip=$((0x1B85EC))
unsquashfs -d firmware/rootfs firmware/rootfs.sqsh
```

### 5.4 Similar firmware and API families

The IBT-200CP is the same UIV3/EasyN/Foscam white-label family:

* **EasyN FS-618A-M136** — the London Hackspace wiki documents the exact
  `viwx.html` / `pant.html` / `image.jpg` / `cmmd` web UI and the
  `uicam query` UDP state protocol. Its banner uses `vid=smarteye` instead of
  `vid=tutkp2p`, so the P2P backend differs, but the web/HTTP/UDP layer is the
  same.
* **EasyN F2 series** — an archived EasyN download page lists
  `F2 Series firware update version`, version `F2-E-21-L1`, file
  `http://www.easyn.com/download/jffs2.img` (5 MB JFFS2). The file itself is
  no longer hosted or archived, but it confirms the firmware format and the
  `jffs2.img` layout.
* **EasyN P2PClient V2.0.28** — downloaded from `easyn.cn/upload/`; contains
  `TUTKNet.dll`, `IOTCAPIs.dll`, `AVAPIs.dll` (newer TUTK IOTC), plus a
  `P2PClient.jmp` symbol map. This is a newer TUTK client, not the 2012
  P2PMJV3 SDK, but it shows the vendor's TUTK integration.
* **EasyN IPCameraSearch** — contains Hi3510 CGI queries
  (`getp2pattr`, `gettp2pattr`, `getsysinfo`) and a firmware upgrade path
  (`POST /upgrade_firmware.cgi?user=`). Our camera's simple UIV3 web server
  does not answer those Hi3510 paths, so this tool is for a different
  generation.
* **Faleemi FSC880** — public research documents the same
  `api/userip.asp` cloud registration on `www.nwsvr1.com`. This is the same
  OEM cloud API family.

These give us the camera-side HTTP/UDP/cloud behavior without the exact
IBT-200CP image. The exact image is still not public; a flash dump would be
needed to confirm the P2P registration packet and the `/api/userip.asp`
response parser.

### 5.5 Can this firmware be used on the IBT-200CP?

No. The IBT-200CP is a 2012 device; the recovered image is for a 2019
Realtek RTS3903 / Tuya camera (I9812). Flashing it would brick the IBT-200CP.
It is included only as evidence of the vendor’s later stack.

---

## 6. iOS side

The old iOS app was **P2P IP Cam Lite**, the same app name as the Android
client. It is no longer on the App Store, and no IPA was found in public
archives. Apple’s iTunes lookup API returns nothing for the likely bundle IDs
(`com.tutk.P2PIPCamLite`, `com.tutk.VideoViewer`, etc.).

This does not block a new iOS app:

* The iOS app used the same TUTK P2P service and the same HTTP-over-UDP
  commands. The 2010 Android Java client is a complete reference.
* The current HSL iOS app is **Netcam** (`com.hsmartlink.LinkCam`, App Store
  ID 1600249620). It uses the newer TUTK PPCS cloud, not the old
  `p2pipcam.com` service.
* If an IPA is still wanted, it must come from an Apple ID that previously
  “purchased” the old app (or from a device backup). `ipatool` can download it
  with such an account, but the binary will be FairPlay-encrypted.

---

## 7. Running your own server

`re/p2p_server.py` implements the old protocol and a relay workaround.

### 7.1 Quick start

Run both the HTTP registration endpoint and the UDP P2P server on the public
host:

```sh
cd re
sudo python3 fake_cloud.py --port 80 --body "" --save registrations.jsonl
python3 p2p_server.py --public-host www.p2pipcam.com --verbose
```

`fake_cloud.py` listens on TCP and UDP port 80 because the camera sends the
raw `GET /api/userip.asp` request over UDP (the same OEM firmware family does
this; the state block contains the exact request). If nginx already owns TCP
80, use `--no-tcp` for UDP 80 and proxy `/api/userip.asp` from nginx to a
second instance on 8080 (`re/nginx-www.p2pipcam.com.conf`). Keep the `www`
record **DNS-only**: Cloudflare and similar proxies do not pass UDP 80 or UDP
30000–30004. `p2p_server.py` needs UDP 30000–30004. Point the `www` A record at the server, power-cycle the
camera, and watch both logs. The default empty `200 OK` is the first response
to try; `--body ok` and `--body source` (echo the camera's public IP) are quick
alternatives.

* `--public-host` must be a hostname or IP that both the phone and the camera
  can reach. Use **`www.p2pipcam.com`**, because that is the hostname the
  camera and app resolve. `usage.p2pipcam.com` is only the server's HTML alias
  and is not the P2P backend.
* The server binds UDP **30000, 30002, 30003, 30004**.
* Point the camera’s server name (`www.p2pipcam.com`) at this machine. The
  cleanest way is a DNS A record, because the firmware may resolve the name at
  boot. For testing, use your router’s DNS override or a hosts file on the
  phone; the camera may have a hardcoded DNS server, so a local DNS server is
  usually needed.
* If the firmware has a hard-coded IP instead of a hostname, DNS will not take
  effect. Capture the destination with `tcpdump` first, then use a router/NAT
  redirect (or a VPN) to send UDP 30000–30004 to this server.

### 7.2 Relay mode (default)

The server answers the query with its own address, then forwards packets
between phone and camera:

```
phone ── query :30002 ──► server
phone ◄─ 0x61/0x63 ───── server
phone ── hole  :30003 ──► server
phone ◄─ 0x06 (server) ── server
phone ── HTTP/video ────► server ──► camera
phone ◄─ video ────────── server ◄── camera
```

This works even when the camera is behind NAT, because the camera already has
a UDP mapping to the server (it registered/keeps alive there). The server uses
that mapping to send packets to the camera.

### 7.3 Direct mode

```sh
python3 p2p_server.py --public-host www.p2pipcam.com --mode direct
```

The server returns the camera’s last-known public IP/port instead of its own.
This only works if the camera’s NAT is open enough, or if the camera and phone
are on the same LAN.

### 7.4 Pinning a camera

If the camera’s registration packet is not recognised, create `devices.json`:

```json
{
  "ABC12345": {"host": "192.168.1.50", "port": 30003}
}
```

and run:

```sh
python3 p2p_server.py --public-host www.p2pipcam.com --devices devices.json -v
```

### 7.5 Self-test

```sh
cd re
python3 selftest.py
```

It starts the server, registers a fake camera, performs query + hole punch,
sends a `GET /cmmd=52` request and verifies the response is relayed back.

### 7.6 What is still unknown

* **Camera registration packet.** The server learns a camera from the first
  packet whose payload starts with a plausible 8-character device ID, and
  treats types `1`, `0xC0`–`0xC3`, `0xD1`, etc. as camera packets. If the real
  camera encrypts its registration, the log will show hex and a pinned mapping
  is needed.
* **HPCMD (type 3).** The original server may send a “connect to this phone”
  command to the camera. The relay mode does not need it because the camera
  replies to the server. `--send-hpcmd` sends an experimental placeholder;
  leave it off unless captures show it is needed.
* **Keepalive interval.** If the camera only registers at boot, start the
  server before powering the camera, or restart the camera after the server is
  up.

### 7.7 Admin monitoring

`p2p_server.py` exposes a read-only dashboard and JSON API:

* `http://127.0.0.1:8081/` — auto-refreshing HTML
* `GET /api/connections` — cameras, phone sessions, counters, last-seen ages
* `GET /api/health` — uptime and counts

Default bind is loopback. To use it from the phone:

```sh
python3 p2p_server.py --public-host www.p2pipcam.com \
  --admin-bind 0.0.0.0 --admin-token 'change-me' -v
```

Send `Authorization: Bearer change-me` (or `?token=change-me`). If it is
internet-facing, terminate TLS in nginx and keep the token in the phone's
Keychain. The dashboard is read-only: no camera control, reboot, or upgrade.

### 7.8 Capture traffic

On the server:

```sh
sudo tcpdump -ni any 'udp portrange 30000-30004' -w p2pipcam.pcap
```

Power-cycle the camera and open the phone app. The pcap will show:

* the camera’s registration/keepalive packet (source = camera, destination =
  server:3000x),
* the phone’s query and hole packets,
* the exact field values for the camera.

Send the pcap (or the hex log from `p2p_server.py -v`) and the server can be
tightened to the real camera.

---

## 8. What the iOS client needs to implement

1. **Query**
   * UDP socket, send `0x60` (old app) or `0x62` (2012 native SDK) to `server:30002` with 40-byte `id + password`.
   * Read `0x61` (100 bytes) and/or `0x63` (180 bytes).
   * Take `p2p1` / `relay1`.
2. **Hole punch**
   * Send `0x02` (new) or `0x04` (old) to `server:30003`.
   * Read `0x06` (40 bytes) → peer IP/port.
3. **HTTP-over-UDP**
   * Send `0x2A` with `GET /cmmd=52&username=<id>& HTTP/1.1` and Basic auth.
   * Send `0x2A` with `GET /image.jpg HTTP/1.1` and Basic auth.
   * Read `0x2A` for HTTP text, `0x21/0x22/0x23` for JPEG data, `0x2C` for ACKs.
4. **Reassembly**
   * `frameSize = ack & 0xFFFFFF`
   * `index = (seq - 230) / 900`
   * Buffer 900-byte chunks, request missing sequence numbers with `0x2C`.
5. **Stop**
   * `GET /stop HTTP/1.1` or type `0x2D` (`MSGTYPE_FIN`).

`re/p2p_client.py` is a small Python reference for steps 1–3.
`re/p2p_server.py` is the server-side reference.

---

## 9. Open questions / next steps

1. Run `fake_cloud.py` on `www.p2pipcam.com` and power-cycle the camera. The
   `/api/userip.asp` request is already known; the missing piece is the
   response body the camera accepts and the next P2P packet it sends.
2. Capture the camera’s UDP 30000–30004 traffic while it points at
   `p2p_server.py`. The first packet from the camera is the P2P registration
   format.
3. If the camera has a local web UI, check for a firmware-update URL
   (`/cgi-bin/...`, `/upgrade`, etc.) before opening the case.
4. If firmware is still needed, dump the SPI flash (likely a 25Q series
   SOIC-8) with a CH341A/clip and `flashrom`. The I9812 uses a 64 Mbit
   (8 MB) flash; the IBT-200CP is probably similar. `binwalk` will find the
   kernel/rootfs.
5. For the iOS app: implement the protocol above, or use a VPN/Tailscale to
   reach the camera’s LAN interface if it exposes RTSP/HTTP. The old P2P
   service is not required for local viewing.

---

## 10. Evidence index

| Path | Contents |
|---|---|
| `web/ibt/IBT-200CP.html` | Archived I-Bt product page (Big5) |
| `apk/jadx_lite/sources/com/tutk/VideoViewer/Protocol.java` | Server + ports + message types |
| `apk/jadx_lite/sources/com/tutk/VideoViewer/GetLocalServ.java` | Query packet |
| `apk/jadx_lite/sources/com/tutk/VideoViewer/ThreadP2PSession.java` | Hole punch, HTTP, reassembly |
| `apk/jadx_lite/sources/com/tutk/VideoViewer/Packet.java` | 12-byte header, 900-byte chunks |
| `apk/jadx_lite2/sources/com/tutk/VideoViewer/AppConst.java` | 2012 master host |
| `apk/jadx_lite2/sources/com/tutk/mjpeg/P2PMJV3APIs.java` | Native SDK surface |
| `apk/libs2/lib/armeabi/libP2PMJV3.so` | 2012 native protocol |
| `apk/jadx_digma/sources/hsl/p2pipcam/manager/DeviceManager.java` | TUTK init string |
| `firmware/rootfs/usr/bin/dgiot` | 2019 Tuya/TUTK firmware binary |
| `re/p2p_server.py` | Runnable rendezvous/relay server |
| `re/p2p_protocol.py` | Packet builders/parsers |
| `re/p2p_client.py` | Reference client |
| `re/selftest.py` | End-to-end test |
