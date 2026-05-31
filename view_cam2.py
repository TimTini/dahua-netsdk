"""
Xem live Dahua qua P2P serial + OpenCV.

Cách hoạt động:
  1. Chạy dh-p2p (tunnel UDP P2P -> RTSP local, mặc định port 8554)
  2. OpenCV đọc rtsp://127.0.0.1:8554/...?channel=2

Cần: user/pass thiết bị (SmartPSS lưu mã hoá, không đọc được từ file config).
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import cv2

from bootstrap import ensure_repo_ready
from paths import REPO_ROOT

ROOT = REPO_ROOT
DH_P2P_DIR = REPO_ROOT / "dh-p2p"
DEFAULT_SERIAL = ""
DEFAULT_LOCAL_PORT = 8554


def build_rtsp_url(
    username: str,
    password: str,
    channel: int,
    subtype: int,
    host: str = "127.0.0.1",
    port: int = DEFAULT_LOCAL_PORT,
) -> str:
    user = quote(username, safe="")
    pwd = quote(password, safe="")
    return (
        f"rtsp://{user}:{pwd}@{host}:{port}/cam/realmonitor"
        f"?channel={channel}&subtype={subtype}"
    )


def check_local_port(port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise RuntimeError(
            f"Cổng local {port} đang bị chiếm. "
            f"Dừng process cũ (view_cam2/dh-p2p) hoặc dùng --local-port khác. ({exc})"
        ) from exc
    finally:
        probe.close()


def start_p2p_tunnel(
    serial: str,
    username: str,
    password: str,
    python_exe: str,
    local_port: int,
    p2p_server: str,
) -> subprocess.Popen[str]:
    main_py = DH_P2P_DIR / "main.py"
    if not main_py.is_file():
        raise FileNotFoundError(
            f"Thiếu {main_py}. Chạy: git clone https://github.com/khoanguyen-3fc/dh-p2p.git "
            f'"{DH_P2P_DIR}"'
        )

    cmd = [
        python_exe,
        "-u",
        str(main_py),
        "-t",
        "0",
        "-u",
        username,
        "-p",
        password,
        "--listen-port",
        str(local_port),
        "--p2p-server",
        p2p_server,
        serial,
    ]
    safe_cmd = " ".join(
        part if part not in (password,) else "***" for part in cmd
    )
    print("Starting P2P tunnel:", safe_cmd, flush=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        cmd,
        cwd=str(DH_P2P_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )


def wait_tunnel_ready(proc: subprocess.Popen[str], timeout_sec: int = 180) -> None:
    assert proc.stdout is not None
    deadline = time.time() + timeout_sec
    last_status = time.time()
    output_lines: list[str] = []

    print("Đang handshake P2P (30–120s là bình thường)...", flush=True)

    while time.time() < deadline:
        if proc.poll() is not None:
            rest = proc.stdout.read()
            if rest:
                print(rest, end="", flush=True)
                output_lines.extend(rest.splitlines())
            tail = "\n".join(output_lines[-20:])
            raise RuntimeError(
                "Tunnel thoát sớm.\n"
                f"{tail}\n"
                "Gợi ý: kiểm tra serial/user/pass; đóng SmartPSS nếu port bị chiếm."
            )

        line = proc.stdout.readline()
        if line:
            print(line, end="", flush=True)
            output_lines.append(line.rstrip("\n"))
            lower = line.lower()
            if "ready to connect" in lower:
                return
            if "only one usage of each socket address" in lower or "winerror 10048" in lower:
                raise RuntimeError(
                    "Port RTSP local bị chiếm (thường 554). "
                    "Dùng --local-port 8554 hoặc tắt process cũ."
                )
            if "error:" in lower or "requires authentication" in lower:
                raise RuntimeError(line.strip())
            if "timeout occurred" in lower:
                raise RuntimeError(line.strip())
            last_status = time.time()
            continue

        if time.time() - last_status >= 10:
            print("... vẫn đang chờ P2P ...", flush=True)
            last_status = time.time()

    raise TimeoutError(
        f"Tunnel chưa sẵn sàng sau {timeout_sec}s. "
        "Thử lại hoặc chạy tunnel riêng: python -u dh-p2p/main.py ..."
    )


def open_capture(rtsp_url: str, retries: int = 30) -> cv2.VideoCapture:
    safe = rtsp_url.split("@", 1)[-1]
    print(f"Kết nối RTSP: ...@{safe}", flush=True)

    for attempt in range(1, retries + 1):
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                print(f"RTSP OK (lần thử {attempt})", flush=True)
                return cap
            cap.release()
        print(f"Chờ RTSP... ({attempt}/{retries})", flush=True)
        time.sleep(2)

    raise RuntimeError(
        "Không mở được RTSP. Kiểm tra user/pass, channel, tunnel đã Ready chưa."
    )


def play_live(cap: cv2.VideoCapture, window: str) -> None:
    print("Esc = thoát", flush=True)
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            print("Mất frame, thử đọc lại...", flush=True)
            time.sleep(0.5)
            continue
        cv2.imshow(window, frame)
        if cv2.waitKey(1) & 0xFF == 27:
            break


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dahua P2P live view (OpenCV)")
    parser.add_argument("--serial", default=DEFAULT_SERIAL, help="Serial P2P")
    parser.add_argument("-u", "--username", default=os.getenv("DAHUA_USER", "admin"))
    parser.add_argument("-p", "--password", default=os.getenv("DAHUA_PASS"))
    parser.add_argument(
        "--channel",
        type=int,
        default=2,
        help="Số kênh Dahua (cam 2 -> channel=2). NetSDK đếm từ 0; RTSP thường từ 1.",
    )
    parser.add_argument(
        "--subtype",
        type=int,
        default=0,
        help="0=main stream, 1=sub stream",
    )
    parser.add_argument(
        "--local-port",
        type=int,
        default=DEFAULT_LOCAL_PORT,
        help=f"Cổng RTSP local (mặc định {DEFAULT_LOCAL_PORT}, tránh 554)",
    )
    parser.add_argument(
        "--p2p-server",
        choices=("easy4ip", "dolynk"),
        default=os.getenv("DAHUA_P2P_SERVER", "easy4ip"),
        help="Cloud P2P: easy4ip (mặc định) hoặc dolynk (SmartPSS Lite mới)",
    )
    parser.add_argument(
        "--tunnel-only",
        action="store_true",
        help="Chỉ chạy tunnel, in URL RTSP rồi giữ process",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = parse_args()
    ensure_repo_ready()
    if not args.password:
        print("Thiếu password. Dùng -p hoặc env DAHUA_PASS.", file=sys.stderr)
        return 2
    if not args.serial:
        print("Thiếu serial P2P. Dùng --serial hoặc env DAHUA_SERIAL.", file=sys.stderr)
        return 2

    check_local_port(args.local_port)
    proc = start_p2p_tunnel(
        args.serial,
        args.username,
        args.password,
        sys.executable,
        args.local_port,
        args.p2p_server,
    )
    try:
        wait_tunnel_ready(proc)
    except Exception:
        proc.terminate()
        proc.wait(timeout=5)
        raise

    url = build_rtsp_url(
        args.username,
        args.password,
        args.channel,
        args.subtype,
        port=args.local_port,
    )
    print("RTSP URL (ffplay test):", url, flush=True)

    if args.tunnel_only:
        print("Tunnel-only. Ctrl+C để dừng.", flush=True)
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
        return 0

    cap = None
    try:
        cap = open_capture(url)
        play_live(cap, f"P2P cam ch{args.channel}")
    finally:
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
        proc.terminate()
        proc.wait(timeout=5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
