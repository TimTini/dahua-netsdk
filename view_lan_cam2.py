"""Xem live qua IP LAN (không P2P). Thử khi máy cùng mạng với NVR/camera."""
from __future__ import annotations

import argparse
import sys
from urllib.parse import quote

import cv2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--ip", required=True, help="IP LAN thiết bị / NVR")
    p.add_argument("-u", "--username", default="admin")
    p.add_argument("-p", "--password", required=True)
    p.add_argument("--channel", type=int, default=2)
    p.add_argument("--subtype", type=int, default=0)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    user = quote(args.username, safe="")
    pwd = quote(args.password, safe="")
    url = (
        f"rtsp://{user}:{pwd}@{args.ip}:554/cam/realmonitor"
        f"?channel={args.channel}&subtype={args.subtype}"
    )
    print("RTSP:", url.split("@", 1)[-1], flush=True)
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print("Không mở RTSP LAN. Kiểm tra IP/user/pass hoặc firewall.", file=sys.stderr)
        return 1
    print("Esc = thoát", flush=True)
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        cv2.imshow(f"LAN cam {args.channel}", frame)
        if cv2.waitKey(1) & 0xFF == 27:
            break
    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
