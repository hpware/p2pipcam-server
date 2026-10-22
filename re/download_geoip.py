#!/usr/bin/env python3
"""Download the MaxMind GeoLite2-Country CSV zip into --dir, refreshed every --days.

Runs as a sidecar container next to p2p_server.py; both share the volume so the
server can enrich its device log and admin dashboard with country data.

    python3 download_geoip.py \
        --url "https://USER:KEY@download.maxmind.com/geoip/databases/GeoLite2-Country-CSV/download?suffix=zip" \
        --dir /geoip --days 14
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
import zipfile


def fetch_curl(url: str, dest: str) -> None:
    """MaxMind's download redirects to S3 (SigV4); curl follows it correctly."""
    subprocess.run(["curl", "-sSfL", "--max-time", "600", "-o", dest, url], check=True)


def needs_refresh(directory: str, days: float) -> bool:
    """True if the zip is missing or older than `days`."""
    path = os.path.join(directory, "GeoLite2-Country-CSV.zip")
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return True
    return age > days * 86400


def download(args_url: str, directory: str) -> None:
    path = os.path.join(directory, "GeoLite2-Country-CSV.zip")
    tmp = path + ".part"
    os.makedirs(directory, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            print("downloading GeoLite2-Country-CSV ...", flush=True)
            fetch_curl(args_url, tmp)
            os.replace(tmp, path)
            with zipfile.ZipFile(path) as zf:
                zf.extractall(directory)
                for name in zf.namelist():
                    extracted = os.path.join(directory, name)
                    if os.path.isfile(extracted):
                        os.utime(extracted, None)
            print("done", flush=True)
            return
        except Exception as exc:  # noqa: BLE001 - single-purpose sidecar
            last_error = exc
            print(f"attempt {attempt + 1} failed: {exc}", flush=True)
            time.sleep(60 * (attempt + 1))
    raise SystemExit(f"geoip download failed: {last_error}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--dir", default="/geoip")
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--check-every", type=float, default=3600,
                    help="seconds between refresh checks")
    args = ap.parse_args()

    if needs_refresh(args.dir, args.days):
        download(args.url, args.dir)
    else:
        print("GeoLite2 CSV still fresh", flush=True)
    while True:
        time.sleep(args.check_every)
        if needs_refresh(args.dir, args.days):
            download(args.url, args.dir)


if __name__ == "__main__":
    main()
