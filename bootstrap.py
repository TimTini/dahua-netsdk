"""
Chuẩn bị môi trường trong repo: thư mục, venv, DLL NetSDK, ffmpeg.
Không dùng đường dẫn cài đặt cố định — chỉ REPO_ROOT + khám phá động.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from paths import (
    CACHE_DIR,
    REPO_ROOT,
    VENDOR_BIN_DIR,
    VENDOR_SDK_DIR,
    ensure_cache,
    ensure_dir,
    ensure_downloads,
    load_local_env,
)

REQUIREMENTS = REPO_ROOT / "requirements.txt"
VENV_DIR = REPO_ROOT / ".venv"
SMARTPSS_INSTALL_CACHE = CACHE_DIR / "smartpss_install.path"

SDK_DLL_NAMES = (
    "dhnetsdk.dll",
    "P2PDll.dll",
    "dhconfigsdk.dll",
    "avnetsdk.dll",
    "dhplay.dll",
    "Infra.dll",
    "NetFrameworkmd.dll",
)


def venv_python() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    kwargs.setdefault("check", True)
    return subprocess.run(cmd, **kwargs)


def ensure_venv() -> Path:
    py = venv_python()
    if py.is_file():
        return py
    ensure_dir(VENV_DIR)
    _run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if not py.is_file():
        raise RuntimeError(f"Không tạo được venv tại {VENV_DIR}")
    _run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
    if REQUIREMENTS.is_file():
        _run([str(py), "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    return py


def _has_netsdk_dll(directory: Path) -> bool:
    return (directory / "dhnetsdk.dll").is_file()


def _read_cached_install() -> Path | None:
    if not SMARTPSS_INSTALL_CACHE.is_file():
        return None
    text = SMARTPSS_INSTALL_CACHE.read_text(encoding="utf-8").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_dir() else None


def _write_cached_install(path: Path) -> None:
    ensure_cache()
    SMARTPSS_INSTALL_CACHE.write_text(str(path.resolve()), encoding="utf-8")


def _path_from_env(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve() if path.is_dir() else None


def _find_on_path(exe_name: str) -> Path | None:
    found = shutil.which(exe_name)
    if not found:
        return None
    return Path(found).resolve().parent


def _find_via_registry() -> Path | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None

    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, base in keys:
        try:
            with winreg.OpenKey(hive, base) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        sub_name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, sub_name) as sub:
                            display = _reg_get(sub, "DisplayName") or ""
                            if "smartpss" not in display.lower():
                                continue
                            loc = _reg_get(sub, "InstallLocation") or _reg_get(sub, "DisplayIcon")
                            if not loc:
                                continue
                            loc_path = Path(loc.strip('"').split(",")[0])
                            if loc_path.is_file():
                                loc_path = loc_path.parent
                            if _has_netsdk_dll(loc_path):
                                return loc_path.resolve()
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _reg_get(key, name: str) -> str | None:
    import winreg

    try:
        val, _ = winreg.QueryValueEx(key, name)
        return str(val) if val else None
    except OSError:
        return None


def _find_under_program_files() -> Path | None:
    roots: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        val = os.environ.get(env_name, "").strip()
        if val:
            roots.append(Path(val))
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in ("SmartPSSLite", "SmartPSS"):
            for candidate in root.glob(f"*/{pattern}"):
                if candidate in seen:
                    continue
                seen.add(candidate)
                if _has_netsdk_dll(candidate):
                    return candidate.resolve()
            direct = root / pattern
            if direct.is_dir() and _has_netsdk_dll(direct):
                return direct.resolve()
    return None


def discover_smartpss_install() -> Path | None:
    cached = _read_cached_install()
    if cached and _has_netsdk_dll(cached):
        return cached

    def _from_exe() -> Path | None:
        exe = _find_on_path("SmartPSSLite.exe")
        return exe.parent if exe else None

    for getter in (
        lambda: _path_from_env("SMARTPSS_INSTALL"),
        lambda: _path_from_env("DAHUA_SDK_DIR"),
        _from_exe,
        _find_via_registry,
        _find_under_program_files,
    ):
        result = getter()
        if result is None:
            continue
        path = Path(result)
        if path.is_file():
            path = path.parent
        if path.is_dir() and _has_netsdk_dll(path):
            _write_cached_install(path)
            return path.resolve()
    return None


def sync_sdk_to_vendor(source: Path) -> Path:
    ensure_dir(VENDOR_SDK_DIR)
    copied = 0
    for name in SDK_DLL_NAMES:
        src = source / name
        if src.is_file():
            shutil.copy2(src, VENDOR_SDK_DIR / name)
            copied += 1
    if not _has_netsdk_dll(VENDOR_SDK_DIR):
        raise FileNotFoundError(f"Không copy được dhnetsdk.dll từ {source}")
    print(f"SDK: đã sync {copied} file → {VENDOR_SDK_DIR}", flush=True)
    _write_cached_install(source)
    return VENDOR_SDK_DIR.resolve()


def ensure_sdk_in_repo() -> Path:
    if _has_netsdk_dll(VENDOR_SDK_DIR):
        return VENDOR_SDK_DIR.resolve()
    external = discover_smartpss_install()
    if external:
        return sync_sdk_to_vendor(external)
    raise FileNotFoundError(
        "Chưa có DLL trong vendor/sdk và không tìm thấy SmartPSS trên máy.\n"
        f"  Đặt file vào: {VENDOR_SDK_DIR}\n"
        "  Hoặc cài SmartPSS rồi chạy lại: python bootstrap.py"
    )


def ensure_ffmpeg_in_repo() -> Path:
    ensure_dir(VENDOR_BIN_DIR)
    local = VENDOR_BIN_DIR / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if local.is_file():
        return local

    on_path = shutil.which("ffmpeg")
    if on_path:
        shutil.copy2(on_path, local)
        return local

    py = venv_python() if venv_python().is_file() else sys.executable
    try:
        _run([str(py), "-m", "pip", "install", "imageio-ffmpeg"], capture_output=True)
        import imageio_ffmpeg

        bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if bundled.is_file():
            shutil.copy2(bundled, local)
            return local
    except Exception as exc:
        print(f"imageio-ffmpeg: {exc}", flush=True)

    if sys.platform == "win32" and shutil.which("winget"):
        try:
            _run(
                [
                    "winget", "install", "--id", "Gyan.FFmpeg", "-e",
                    "--accept-source-agreements", "--accept-package-agreements",
                ],
                capture_output=True,
            )
            on_path = shutil.which("ffmpeg")
            if on_path:
                shutil.copy2(on_path, local)
                return local
        except subprocess.CalledProcessError:
            pass

    raise FileNotFoundError(
        f"Không có ffmpeg. Cài thủ công vào {local} hoặc chạy: python bootstrap.py"
    )


def ensure_repo_ready() -> None:
    load_local_env()
    ensure_downloads()
    ensure_cache()
    ensure_dir(VENDOR_SDK_DIR)
    ensure_dir(VENDOR_BIN_DIR)
    ensure_venv()
    ensure_sdk_in_repo()
    ensure_ffmpeg_in_repo()


def main() -> int:
    try:
        ensure_repo_ready()
        print("Bootstrap OK.", flush=True)
        print(f"  repo   = {REPO_ROOT}", flush=True)
        print(f"  sdk    = {VENDOR_SDK_DIR}", flush=True)
        print(f"  ffmpeg = {VENDOR_BIN_DIR / 'ffmpeg.exe'}", flush=True)
        print(f"  venv   = {venv_python()}", flush=True)
        return 0
    except Exception as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
