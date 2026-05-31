"""Đường dẫn động — mọi thứ dưới REPO_ROOT; thiếu thì bootstrap cài/sync."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DOWNLOADS_DIR = REPO_ROOT / "downloads"
CACHE_DIR = REPO_ROOT / ".cache"
VENDOR_SDK_DIR = REPO_ROOT / "vendor" / "sdk"
VENDOR_BIN_DIR = REPO_ROOT / "vendor" / "bin"
LOCAL_ENV_FILE = REPO_ROOT / "config.local.env"
SMARTPSS_INSTALL_CACHE = CACHE_DIR / "smartpss_install.path"


def load_local_env() -> None:
    if not LOCAL_ENV_FILE.is_file():
        return
    for line in LOCAL_ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_local_env()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_downloads() -> Path:
    return ensure_dir(DOWNLOADS_DIR)


def ensure_cache() -> Path:
    return ensure_dir(CACHE_DIR)


def _has_netsdk_dll(directory: Path) -> bool:
    return (directory / "dhnetsdk.dll").is_file()


def _resolve_optional_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def resolve_sdk_dir(explicit: str | Path | None = None, *, auto_bootstrap: bool = True) -> Path:
    if explicit:
        path = _resolve_optional_path(explicit)
        if _has_netsdk_dll(path):
            return path
        raise FileNotFoundError(f"Không có dhnetsdk.dll trong {path}")

    if _has_netsdk_dll(VENDOR_SDK_DIR):
        return VENDOR_SDK_DIR.resolve()

    if auto_bootstrap:
        from bootstrap import ensure_sdk_in_repo

        return ensure_sdk_in_repo()

    raise FileNotFoundError(
        f"Chưa có SDK trong {VENDOR_SDK_DIR}. Chạy: python bootstrap.py"
    )


def resolve_smartpss_log_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        path = _resolve_optional_path(explicit)
        if path.is_dir():
            return path
        raise FileNotFoundError(f"Không có thư mục log: {path}")

    env_log = os.environ.get("SMARTPSS_LOG_DIR", "").strip()
    if env_log:
        path = _resolve_optional_path(env_log)
        if path.is_dir():
            return path

    if SMARTPSS_INSTALL_CACHE.is_file():
        install = Path(SMARTPSS_INSTALL_CACHE.read_text(encoding="utf-8").strip())
        log_dir = install / "Log" / "client_log"
        if log_dir.is_dir():
            return log_dir.resolve()

    sdk = resolve_sdk_dir()
    log_dir = sdk / "Log" / "client_log"
    if log_dir.is_dir():
        return log_dir.resolve()

    raise FileNotFoundError(
        "Không tìm thấy log SmartPSS. Chạy SmartPSS, hoặc trong config.local.env:\n"
        f"  SMARTPSS_LOG_DIR=đường_dẫn_tương_đối_trong_repo"
    )


def ffmpeg_exe(*, auto_bootstrap: bool = True) -> Path:
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    local = VENDOR_BIN_DIR / name
    if local.is_file():
        return local
    if auto_bootstrap:
        from bootstrap import ensure_ffmpeg_in_repo

        return ensure_ffmpeg_in_repo()
    raise FileNotFoundError(f"Chưa có ffmpeg trong {local}. Chạy: python bootstrap.py")


def prepend_vendor_bin_to_path() -> None:
    ensure_dir(VENDOR_BIN_DIR)
    bin_str = str(VENDOR_BIN_DIR)
    current = os.environ.get("PATH", "")
    if bin_str not in current.split(os.pathsep):
        os.environ["PATH"] = bin_str + os.pathsep + current


def new_download_path(channel_ui: int, suffix: str) -> Path:
    ensure_downloads()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = suffix.lstrip(".")
    return DOWNLOADS_DIR / f"clip_ch{channel_ui}_{stamp}.{ext}"


def resolve_output_path(
    user_path: str | Path | None,
    channel_ui: int,
    want_mp4: bool,
) -> Path:
    if user_path:
        path = _resolve_optional_path(user_path)
        if want_mp4 and path.suffix.lower() == ".mp4":
            dav = path.with_suffix(".dav")
        elif path.suffix.lower() == ".dav":
            dav = path
        else:
            dav = path.with_suffix(".dav")
        ensure_dir(dav.parent)
        return dav
    return new_download_path(channel_ui, "dav")
