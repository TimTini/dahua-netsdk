"""Tải clip lịch sử bằng NetSDK. Output: downloads/ trong repo."""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from bootstrap import ensure_repo_ready
from netsdk.dahua_netsdk import DahuaNetSDK
from paths import DOWNLOADS_DIR, ffmpeg_exe, prepend_vendor_bin_to_path, resolve_output_path, resolve_sdk_dir

DEFAULT_SERIAL = ""


def ui_channel_to_sdk(channel_ui: int) -> int:
    return channel_ui - 1 if channel_ui > 0 else channel_ui


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dahua NetSDK playback download")
    p.add_argument("-u", "--username", default="admin")
    p.add_argument("-p", "--password", required=True)
    p.add_argument("--serial", default=DEFAULT_SERIAL)
    p.add_argument("--ip", default="")
    p.add_argument("--port", type=int, default=37777)
    p.add_argument("--channel-ui", type=int, default=2)
    p.add_argument("--channel-sdk", type=int, default=-1)
    p.add_argument("--minutes-ago", type=float, default=1.0)
    p.add_argument("--duration-sec", type=int, default=60)
    p.add_argument("-o", "--output", default="", help="vd. downloads/ten.dav (tương đối repo)")
    p.add_argument("--mp4", action="store_true")
    p.add_argument("--sdk-dir", default="", help="vd. vendor/sdk (mặc định auto)")
    p.add_argument("--no-bootstrap", action="store_true", help="Không tự cài/sync")
    return p.parse_args()


def dav_to_mp4(dav: Path) -> Path:
    ff = ffmpeg_exe()
    mp4 = dav.with_suffix(".mp4")
    subprocess.run([str(ff), "-y", "-i", str(dav), "-c", "copy", str(mp4)], check=True, capture_output=True)
    return mp4


def main() -> int:
    args = parse_args()
    if not args.no_bootstrap:
        ensure_repo_ready()
        prepend_vendor_bin_to_path()

    channel = args.channel_sdk if args.channel_sdk >= 0 else ui_channel_to_sdk(args.channel_ui)
    end = datetime.now() - timedelta(minutes=args.minutes_ago)
    start = end - timedelta(seconds=args.duration_sec)
    out = resolve_output_path(args.output or None, args.channel_ui, args.mp4)

    sdk_dir = resolve_sdk_dir(args.sdk_dir or None, auto_bootstrap=not args.no_bootstrap)
    sdk = DahuaNetSDK(sdk_dir)
    print(f"SDK: {sdk_dir}", flush=True)
    print(f"Downloads: {DOWNLOADS_DIR}", flush=True)

    sdk.init()
    try:
        if args.ip:
            dev = sdk.login(args.ip, args.port, args.username, args.password, p2p=False)
        elif args.serial:
            dev = sdk.login(args.serial, args.port, args.username, args.password, p2p=True)
        else:
            raise SystemExit("Cần --ip (127.0.0.1 + port SmartPSS) hoặc --serial")

        serial = bytes(dev.sSerialNumber).split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        print(f"Login OK channels={dev.nChanNum} serial={serial!r}", flush=True)
        path = sdk.download_by_time(channel, start, end, out)
        print(f"Saved: {path} ({path.stat().st_size} bytes)", flush=True)
        if args.mp4:
            mp4 = dav_to_mp4(path)
            print(f"MP4: {mp4} ({mp4.stat().st_size} bytes)", flush=True)
        return 0
    finally:
        sdk.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
