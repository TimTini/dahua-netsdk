"""Test login NetSDK."""
from __future__ import annotations

import argparse
import sys

from bootstrap import ensure_repo_ready
from netsdk.dahua_netsdk import DahuaNetSDK
from paths import resolve_sdk_dir


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("-u", default="admin")
    p.add_argument("-p", required=True)
    p.add_argument("--serial", default="")
    p.add_argument("--ip", default="")
    p.add_argument("--port", type=int, default=37777)
    p.add_argument("--sdk-dir", default="")
    p.add_argument("--no-bootstrap", action="store_true")
    args = p.parse_args()

    if not args.no_bootstrap:
        ensure_repo_ready()

    sdk_dir = resolve_sdk_dir(args.sdk_dir or None, auto_bootstrap=not args.no_bootstrap)
    sdk = DahuaNetSDK(sdk_dir)
    print(f"SDK: {sdk_dir}", flush=True)
    sdk.init()
    try:
        if args.ip:
            dev = sdk.login(args.ip, args.port, args.u, args.p, p2p=False)
            mode = f"{args.ip}:{args.port}"
        elif args.serial:
            dev = sdk.login(args.serial, args.port, args.u, args.p, p2p=True)
            mode = f"P2P {args.serial}"
        else:
            raise SystemExit("Cần --ip hoặc --serial")

        serial = bytes(dev.sSerialNumber).split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        print(f"OK {mode} login_id={sdk.login_id} channels={dev.nChanNum} serial={serial!r}")
        return 0
    finally:
        sdk.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
