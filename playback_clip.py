"""
Download Dahua playback clip qua RTSP.

CLI-only mode:
- direct LAN: dùng --host/--port.
- P2P: dùng --serial, script sẽ tự chạy dh-p2p tunnel local, không cần SmartPSS GUI.
"""
from __future__ import annotations

import argparse
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from paths import REPO_ROOT, ffmpeg_exe, new_download_path, prepend_vendor_bin_to_path


class ClipDownloadError(RuntimeError):
    """Raised when the Dahua clip cannot be downloaded."""


class ClipConfigError(ClipDownloadError):
    """Raised when required Dahua CLI config is missing."""


def dahua_time(dt: datetime) -> str:
    return dt.strftime("%Y_%m_%d_%H_%M_%S")


def build_playback_url(
    host: str,
    port: int,
    username: str,
    password: str,
    channel: int,
    start: datetime,
    end: datetime,
) -> str:
    user = quote(username, safe="")
    pwd = quote(password, safe="")
    return (
        f"rtsp://{user}:{pwd}@{host}:{port}/cam/playback"
        f"?channel={channel}"
        f"&starttime={dahua_time(start)}"
        f"&endtime={dahua_time(end)}"
    )


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _resolve_output_path(raw: str, channel: int) -> Path:
    if raw:
        out_path = Path(raw).expanduser()
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return out_path.resolve(strict=False)
    return new_download_path(channel, "mp4").resolve(strict=False)


def _resolve_ffmpeg(raw: str = "") -> str:
    raw = raw.strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(raw)
        if found:
            return found
        return raw
    return str(ffmpeg_exe())


def _resolve_ffprobe(ffmpeg_bin: str) -> str:
    exe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    ffmpeg_path = Path(ffmpeg_bin)
    if ffmpeg_path.name:
        candidate = ffmpeg_path.with_name(exe_name)
        if candidate.is_file():
            return str(candidate)
    return shutil.which(exe_name) or shutil.which("ffprobe") or exe_name


def _probe_media_duration(path: Path, ffmpeg_bin: str) -> float | None:
    try:
        proc = subprocess.run(
            [
                _resolve_ffprobe(ffmpeg_bin),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        return float((proc.stdout or "").strip())
    except ValueError:
        return None


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _sanitize(text: str | bytes, password: str = "", url: str = "", serial: str = "") -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    redacted = str(text)
    if url:
        redacted = redacted.replace(url, "<rtsp-url-redacted>")
    if password:
        redacted = redacted.replace(password, "<redacted>")
        redacted = redacted.replace(quote(password, safe=""), "<redacted>")
    if serial:
        redacted = redacted.replace(serial, "<serial-redacted>")
    return redacted.strip() or "ffmpeg failed"


@dataclass(frozen=True)
class P2PTunnelConfig:
    serial: str
    username: str
    password: str
    listen_port: int = 8554
    remote_port: int = 554
    p2p_server: str = "easy4ip"
    p2p_type: int = 0
    relay: bool = True
    start_timeout_sec: int = 180


class P2PTunnel:
    """Run bundled dh-p2p as a SmartPSS-like local RTSP tunnel, without GUI."""

    def __init__(self, config: P2PTunnelConfig) -> None:
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str] = queue.Queue()
        self._recent_lines: list[str] = []

    def __enter__(self) -> "P2PTunnel":
        script = REPO_ROOT / "dh-p2p" / "main.py"
        if not script.is_file():
            raise ClipConfigError(f"Missing dh-p2p CLI: {script}")

        cmd = [
            sys.executable,
            "-u",
            str(script),
            "--listen-port",
            str(self.config.listen_port),
            "--remote-port",
            str(self.config.remote_port),
            "--p2p-server",
            self.config.p2p_server,
            "--type",
            str(self.config.p2p_type),
            "--username",
            self.config.username,
            "--password",
            self.config.password,
        ]
        if self.config.relay:
            cmd.append("--relay")
        cmd.append(self.config.serial)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self.process = subprocess.Popen(
            cmd,
            cwd=str(script.parent),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._pump_output, daemon=True).start()
        self._wait_until_ready()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        proc = self.process
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def _pump_output(self) -> None:
        proc = self.process
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            self._lines.put(line.rstrip())

    def _wait_until_ready(self) -> None:
        deadline = time.time() + self.config.start_timeout_sec
        while time.time() < deadline:
            self._drain_lines()
            proc = self.process
            if proc is not None and proc.poll() is not None:
                raise ClipDownloadError(f"dh-p2p exited early: {self.recent_output()}")
            if any("Ready to connect" in line for line in self._recent_lines):
                return
            time.sleep(0.2)
        raise ClipDownloadError(f"dh-p2p start timeout: {self.recent_output()}")

    def _drain_lines(self) -> None:
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                break
            self._recent_lines.append(_sanitize(line, self.config.password, serial=self.config.serial))
            self._recent_lines = self._recent_lines[-40:]

    def recent_output(self) -> str:
        self._drain_lines()
        return " | ".join(self._recent_lines[-12:]) or "no output"


def _run_ffmpeg_process(
    cmd: list[str],
    timeout_sec: int,
    *,
    password: str,
    url: str,
    serial: str,
) -> tuple[bool, str]:
    timed_out = False
    stdout = ""
    stderr = ""
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout, stderr = proc.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stderr = _sanitize(exc.stderr or "", password, url, serial)
        if proc is not None and proc.poll() is None:
            try:
                if proc.stdin is not None:
                    proc.stdin.write("q\n")
                    proc.stdin.flush()
                extra_stdout, extra_stderr = proc.communicate(timeout=15)
                stdout = (stdout or "") + (extra_stdout or "")
                stderr = (stderr or "") + (extra_stderr or "")
            except Exception:
                proc.terminate()
                try:
                    extra_stdout, extra_stderr = proc.communicate(timeout=5)
                    stdout = (stdout or "") + (extra_stdout or "")
                    stderr = (stderr or "") + (extra_stderr or "")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    extra_stdout, extra_stderr = proc.communicate(timeout=5)
                    stdout = (stdout or "") + (extra_stdout or "")
                    stderr = (stderr or "") + (extra_stderr or "")

    returncode = proc.returncode if proc is not None else 1
    if returncode != 0 and not timed_out:
        raise ClipDownloadError(_sanitize(stderr or "ffmpeg failed", password, url, serial)[-1500:])
    return timed_out, _sanitize(stderr or "", password, url, serial)


def download_ffmpeg(
    url: str,
    out_path: Path,
    duration_sec: int,
    *,
    ffmpeg_bin: str,
    timeout_sec: int,
    password: str,
    serial: str = "",
) -> None:
    """Capture RTSP to TS first, then remux to seekable MP4."""
    capture_path = out_path.with_name(f"{out_path.stem}.capture.ts")
    _safe_unlink(capture_path)
    _safe_unlink(out_path)

    capture_cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-rtsp_transport",
        "tcp",
        "-fflags",
        "+genpts",
        "-use_wallclock_as_timestamps",
        "1",
        "-t",
        str(duration_sec),
        "-i",
        url,
        "-map",
        "0:v:0",
        "-an",
        "-c",
        "copy",
        "-f",
        "mpegts",
        str(capture_path),
    ]
    timed_out, stderr = _run_ffmpeg_process(
        capture_cmd,
        timeout_sec,
        password=password,
        url=url,
        serial=serial,
    )

    try:
        if not capture_path.is_file() or capture_path.stat().st_size < 1000:
            _safe_unlink(capture_path)
            detail = f": {stderr[-1200:]}" if stderr and stderr != "ffmpeg failed" else ""
            if timed_out:
                raise ClipDownloadError(f"ffmpeg timeout after {timeout_sec}s{detail}")
            raise ClipDownloadError(detail.lstrip(": ") or "Empty clip - no usable video stream.")

        min_duration = max(1.0, duration_sec * 0.7)
        duration = _probe_media_duration(capture_path, ffmpeg_bin)
        if duration is None:
            _safe_unlink(capture_path)
            raise ClipDownloadError("Captured clip has no readable duration.")
        if duration < min_duration:
            _safe_unlink(capture_path)
            raise ClipDownloadError(f"Clip too short ({duration:.1f}s).")

        remux_cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(capture_path),
            "-t",
            str(duration_sec),
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
        proc = subprocess.run(
            remux_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(30, duration_sec + 30),
        )
        if proc.returncode != 0:
            _safe_unlink(out_path)
            raise ClipDownloadError(_sanitize(proc.stderr or "ffmpeg remux failed", password, url, serial)[-1200:])
    finally:
        _safe_unlink(capture_path)

    if not out_path.is_file() or out_path.stat().st_size < 1000:
        _safe_unlink(out_path)
        raise ClipDownloadError("Empty clip - check channel, camera time, recording schedule, and RTSP access.")

    duration = _probe_media_duration(out_path, ffmpeg_bin)
    min_duration = max(1.0, duration_sec * 0.7)
    if duration is None:
        _safe_unlink(out_path)
        raise ClipDownloadError("MP4 duration is unreadable.")
    if duration < min_duration:
        _safe_unlink(out_path)
        raise ClipDownloadError(f"Clip too short ({duration:.1f}s).")


def parse_args() -> argparse.Namespace:
    serial_default = _env_first("DAHUA_SERIAL", "DAHUA_DEVICE_SERIAL")
    p = argparse.ArgumentParser(description="Download Dahua playback clip (LAN or CLI-only P2P)")
    p.add_argument("-u", "--username", default=_env_first("DAHUA_USERNAME", "DAHUA_USER", default="admin") or "admin")
    p.add_argument("-p", "--password", default=_env_first("DAHUA_PASSWORD", "DAHUA_PASS"))
    p.add_argument("--channel", type=int, default=_env_int("DAHUA_CHANNEL_UI", _env_int("DAHUA_CHANNEL", 2)))
    p.add_argument("--mode", "--connect-mode", choices=("auto", "direct", "p2p"), default=_env_first("DAHUA_CONNECT_MODE", default="auto"))
    p.add_argument("--host", default=_env_first("DAHUA_RTSP_HOST", "DAHUA_HOST", "DAHUA_LAN_IP"), help="Camera LAN host, or 127.0.0.1 when using an external tunnel")
    p.add_argument("--port", type=int, default=_env_int("DAHUA_RTSP_PORT", 554))
    p.add_argument("--serial", default=serial_default, help="Camera serial. If set with auto/p2p mode, dh-p2p tunnel is started automatically")
    p.add_argument("--p2p-server", default=_env_first("DAHUA_P2P_SERVER", default="easy4ip"), help="P2P cloud: easy4ip or dolynk")
    p.add_argument("--p2p-type", type=int, default=_env_int("DAHUA_P2P_TYPE", 0))
    p.add_argument("--local-port", type=int, default=_env_int("DAHUA_P2P_LISTEN_PORT", 8554), help="Local RTSP port for dh-p2p")
    relay_group = p.add_mutually_exclusive_group()
    relay_group.add_argument("--relay", dest="relay", action="store_true", default=_env_bool("DAHUA_P2P_RELAY", True), help="Use P2P relay channel (default)")
    relay_group.add_argument("--no-relay", dest="relay", action="store_false", help="Try direct UDP hole punch instead of relay")
    p.add_argument("--p2p-start-timeout-sec", type=int, default=_env_int("DAHUA_P2P_START_TIMEOUT_SEC", 180))
    p.add_argument("--minutes-ago", type=float, default=_env_float("DAHUA_RECORD_MINUTES_AGO", 1.0))
    p.add_argument("--duration-sec", type=int, default=_env_int("DAHUA_RECORD_DURATION_SEC", 60))
    p.add_argument("--timeout-sec", type=int, default=_env_int("DAHUA_RECORD_TIMEOUT_SEC", 180))
    p.add_argument("--retries", type=int, default=_env_int("DAHUA_RECORD_RETRIES", 4 if serial_default else 1))
    p.add_argument("--ffmpeg", default=_env_first("DAHUA_FFMPEG_BIN", "FFMPEG_BIN"))
    p.add_argument(
        "-o",
        "--output",
        default="",
        help="Default: downloads/clip_ch{N}_timestamp.mp4",
    )
    return p.parse_args()


def _validate_args(parser_args: argparse.Namespace) -> str:
    if not parser_args.password:
        raise ClipConfigError("Missing password. Use -p/--password or DAHUA_PASS.")

    mode = (parser_args.mode or "auto").lower()
    if mode == "auto":
        mode = "p2p" if parser_args.serial else "direct"
    if mode == "p2p" and not parser_args.serial:
        raise ClipConfigError("Missing serial for P2P mode. Use --serial or DAHUA_SERIAL.")
    if mode == "direct" and not parser_args.host:
        raise ClipConfigError("Missing host for direct mode. Use --host or DAHUA_LAN_IP, or use --serial for P2P.")

    parser_args.duration_sec = max(1, int(parser_args.duration_sec))
    parser_args.timeout_sec = max(parser_args.duration_sec + 30, int(parser_args.timeout_sec))
    parser_args.retries = max(1, int(parser_args.retries))
    parser_args.p2p_start_timeout_sec = max(10, int(parser_args.p2p_start_timeout_sec))
    return mode


def _download_once(args: argparse.Namespace, mode: str, url: str, out_path: Path, ffmpeg_bin: str) -> None:
    if mode == "p2p":
        tunnel_config = P2PTunnelConfig(
            serial=args.serial,
            username=args.username,
            password=args.password,
            listen_port=args.local_port,
            remote_port=args.port,
            p2p_server=args.p2p_server,
            p2p_type=args.p2p_type,
            relay=args.relay,
            start_timeout_sec=args.p2p_start_timeout_sec,
        )
        with P2PTunnel(tunnel_config) as tunnel:
            try:
                download_ffmpeg(
                    url,
                    out_path,
                    args.duration_sec,
                    ffmpeg_bin=ffmpeg_bin,
                    timeout_sec=args.timeout_sec,
                    password=args.password,
                    serial=args.serial,
                )
            except ClipDownloadError as exc:
                raise ClipDownloadError(f"{exc}\nP2P tunnel: {tunnel.recent_output()}") from exc
    else:
        download_ffmpeg(
            url,
            out_path,
            args.duration_sec,
            ffmpeg_bin=ffmpeg_bin,
            timeout_sec=args.timeout_sec,
            password=args.password,
            serial=args.serial,
        )


def main() -> int:
    prepend_vendor_bin_to_path()
    args = parse_args()
    try:
        mode = _validate_args(args)
        try:
            ffmpeg_bin = _resolve_ffmpeg(args.ffmpeg)
        except Exception as exc:
            raise ClipConfigError(f"Missing ffmpeg: {exc}") from exc
        end = datetime.now() - timedelta(minutes=args.minutes_ago)
        start = end - timedelta(seconds=args.duration_sec)
        out_path = _resolve_output_path(args.output, args.channel)

        rtsp_host = "127.0.0.1" if mode == "p2p" else args.host
        rtsp_port = args.local_port if mode == "p2p" else args.port
        url = build_playback_url(
            rtsp_host,
            rtsp_port,
            args.username,
            args.password,
            args.channel,
            start,
            end,
        )

        print(f"Mode: {mode} ({'relay' if args.relay else 'direct-udp'} P2P)" if mode == "p2p" else "Mode: direct", flush=True)
        print(f"Playback: ...@{url.split('@', 1)[-1]}", flush=True)
        print(f"Window: {start:%Y-%m-%d %H:%M:%S} -> {end:%Y-%m-%d %H:%M:%S} ({args.duration_sec}s)", flush=True)
        print(f"Output: {out_path}", flush=True)

        last_error: Exception | None = None
        for attempt in range(1, args.retries + 1):
            try:
                if attempt > 1:
                    print(f"Retry {attempt}/{args.retries}...", flush=True)
                _download_once(args, mode, url, out_path, ffmpeg_bin)
                print(f"OK: {out_path} ({out_path.stat().st_size} bytes)", flush=True)
                return 0
            except ClipDownloadError as exc:
                last_error = exc
                _safe_unlink(out_path)
                if attempt < args.retries:
                    print(f"Attempt {attempt}/{args.retries} failed: {_sanitize(str(exc), args.password, url, args.serial)[-1200:]}", file=sys.stderr, flush=True)
                    time.sleep(min(2.0, 0.5 * attempt))

        assert last_error is not None
        raise last_error
    except ClipDownloadError as exc:
        print(
            "Không tải được Dahua record: "
            f"{_sanitize(str(exc), getattr(args, 'password', ''), locals().get('url', ''), getattr(args, 'serial', ''))[-2000:]}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
