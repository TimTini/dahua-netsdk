"""Dahua NetSDK ctypes — DLL trong vendor/sdk hoặc DAHUA_SDK_DIR."""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    byref,
    c_byte,
    c_char,
    c_char_p,
    c_int,
    c_long,
    c_longlong,
    c_uint,
    c_void_p,
    c_ushort,
)
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from paths import resolve_sdk_dir  # noqa: E402

EM_LOGIN_SPEC_CAP_TCP = 0
EM_LOGIN_SPEC_CAP_P2P = 19
EM_RECORD_TYPE_ALL = 0


class NET_TIME(Structure):
    _fields_ = [
        ("dwYear", c_uint),
        ("dwMonth", c_uint),
        ("dwDay", c_uint),
        ("dwHour", c_uint),
        ("dwMinute", c_uint),
        ("dwSecond", c_uint),
    ]


class NET_DEVICEINFO_Ex(Structure):
    _fields_ = [
        ("sSerialNumber", c_byte * 48),
        ("nAlarmInPortNum", c_int),
        ("nAlarmOutPortNum", c_int),
        ("nDiskNum", c_int),
        ("nDVRType", c_int),
        ("nChanNum", c_int),
        ("nLimitLoginTime", c_byte),
        ("nLeftLoginTime", c_byte),
        ("nMaxLoginSession", c_byte),
        ("nReserved", c_byte),
        ("nLockLeftTime", c_int),
        ("Reserved", c_byte * 24),
    ]


class NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY(Structure):
    _fields_ = [
        ("dwSize", c_uint),
        ("szIP", c_char * 64),
        ("nPort", c_int),
        ("szUserName", c_char * 64),
        ("szPassword", c_char * 64),
        ("emSpecCap", c_int),
        ("byReserved", c_byte * 4),
        ("pCapParam", c_void_p),
    ]


class NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY(Structure):
    _fields_ = [
        ("dwSize", c_uint),
        ("stuDeviceInfo", NET_DEVICEINFO_Ex),
        ("nError", c_int),
        ("byReserved", c_byte * 132),
    ]


def _to_net_time(dt: datetime) -> NET_TIME:
    t = NET_TIME()
    t.dwYear = dt.year
    t.dwMonth = dt.month
    t.dwDay = dt.day
    t.dwHour = dt.hour
    t.dwMinute = dt.minute
    t.dwSecond = dt.second
    return t


def _cstr_bytes(text: str, max_len: int) -> bytes:
    raw = text.encode("ascii")
    if len(raw) >= max_len:
        raise ValueError(f"Chuỗi quá dài ({len(raw)} >= {max_len})")
    return raw + b"\x00" * (max_len - len(raw))


class DahuaNetSDK:
    def __init__(self, sdk_dir: Path | str | None = None) -> None:
        self.sdk_dir = resolve_sdk_dir(sdk_dir)
        dll_path = self.sdk_dir / "dhnetsdk.dll"
        from paths import VENDOR_BIN_DIR, ensure_dir

        os.add_dll_directory(str(self.sdk_dir))
        ensure_dir(VENDOR_BIN_DIR)
        extra = os.pathsep.join((str(self.sdk_dir), str(VENDOR_BIN_DIR)))
        os.environ["PATH"] = extra + os.pathsep + os.environ.get("PATH", "")
        self.dll = ctypes.WinDLL(str(dll_path))
        self._login_id = c_longlong(0)
        self._download_done = False
        self._download_progress = (0, 0)
        self._download_pos_cb = CFUNCTYPE(
            None, c_longlong, c_uint, c_uint, c_int, c_void_p
        )(self._on_download_pos)
        self._setup_prototypes()

    def _setup_prototypes(self) -> None:
        self.dll.CLIENT_InitEx.argtypes = [c_void_p, c_void_p, c_void_p]
        self.dll.CLIENT_InitEx.restype = c_int
        self.dll.CLIENT_Cleanup.argtypes = []
        self.dll.CLIENT_Cleanup.restype = c_int
        self.dll.CLIENT_GetLastError.argtypes = []
        self.dll.CLIENT_GetLastError.restype = c_uint
        self.dll.CLIENT_SetConnectTime.argtypes = [c_uint, c_uint]
        self.dll.CLIENT_SetConnectTime.restype = c_int

        self.dll.CLIENT_LoginWithHighLevelSecurity.argtypes = [
            POINTER(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY),
            POINTER(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY),
        ]
        self.dll.CLIENT_LoginWithHighLevelSecurity.restype = c_longlong

        self.dll.CLIENT_Logout.argtypes = [c_longlong]
        self.dll.CLIENT_Logout.restype = c_int

        self.dll.CLIENT_DownloadByTimeEx.argtypes = [
            c_longlong,
            c_int,
            c_int,
            POINTER(NET_TIME),
            POINTER(NET_TIME),
            c_char_p,
            c_void_p,
            c_void_p,
            c_void_p,
            c_void_p,
            c_void_p,
        ]
        self.dll.CLIENT_DownloadByTimeEx.restype = c_longlong
        self.dll.CLIENT_StopDownload.argtypes = [c_longlong]
        self.dll.CLIENT_StopDownload.restype = c_int

    def _on_download_pos(
        self,
        _handle: int,
        total: int,
        downloaded: int,
        _index: int,
        _user: int,
    ) -> None:
        self._download_progress = (downloaded, total)
        if total > 0 and downloaded >= total:
            self._download_done = True

    def init(self, wait_ms: int = 15000, tries: int = 3) -> None:
        if not self.dll.CLIENT_InitEx(None, None, None):
            raise RuntimeError(f"CLIENT_InitEx failed: 0x{self.dll.CLIENT_GetLastError():x}")
        self.dll.CLIENT_SetConnectTime(wait_ms, tries)

    def cleanup(self) -> None:
        self.logout()
        self.dll.CLIENT_Cleanup()

    def login(
        self,
        host_or_serial: str,
        port: int,
        username: str,
        password: str,
        p2p: bool = False,
    ) -> NET_DEVICEINFO_Ex:
        self.logout()
        stu_in = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stu_in.dwSize = ctypes.sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
        stu_in.szIP = _cstr_bytes(host_or_serial, 64)
        stu_in.nPort = port
        stu_in.szUserName = _cstr_bytes(username, 64)
        stu_in.szPassword = _cstr_bytes(password, 64)
        stu_in.emSpecCap = EM_LOGIN_SPEC_CAP_P2P if p2p else EM_LOGIN_SPEC_CAP_TCP
        stu_in.pCapParam = None

        stu_out = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stu_out.dwSize = ctypes.sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

        login_id = self.dll.CLIENT_LoginWithHighLevelSecurity(byref(stu_in), byref(stu_out))
        if login_id == 0:
            raise RuntimeError(
                f"Login failed host={host_or_serial} port={port} p2p={p2p} "
                f"nError={stu_out.nError} last=0x{self.dll.CLIENT_GetLastError():x}"
            )
        self._login_id = c_longlong(login_id)
        time.sleep(1.0)
        return stu_out.stuDeviceInfo

    def logout(self) -> None:
        if self._login_id.value:
            self.dll.CLIENT_Logout(self._login_id)
            self._login_id = c_longlong(0)

    @property
    def login_id(self) -> int:
        return self._login_id.value

    def download_by_time(
        self,
        channel: int,
        start: datetime,
        end: datetime,
        output_path: Path,
        timeout_sec: int = 300,
    ) -> Path:
        if not self.login_id:
            raise RuntimeError("Chưa login")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        st = _to_net_time(start)
        et = _to_net_time(end)

        self._download_done = False
        self._download_progress = (0, 0)

        handle = self.dll.CLIENT_DownloadByTimeEx(
            self._login_id,
            channel,
            EM_RECORD_TYPE_ALL,
            byref(st),
            byref(et),
            str(output_path).encode("mbcs"),
            self._download_pos_cb,
            None,
            None,
            None,
            None,
        )
        if handle == 0:
            raise RuntimeError(f"DownloadByTimeEx failed: 0x{self.dll.CLIENT_GetLastError():x}")

        deadline = time.time() + timeout_sec
        last_log = 0.0
        while time.time() < deadline:
            if self._download_done:
                break
            if output_path.is_file():
                size = output_path.stat().st_size
                if size > 50000 and self._download_progress[1] == 0:
                    time.sleep(2.0)
                    if output_path.stat().st_size == size:
                        break
            now = time.time()
            if now - last_log >= 5.0:
                d, t = self._download_progress
                print(f"  download {d}/{t} bytes, file={output_path.stat().st_size if output_path.is_file() else 0}", flush=True)
                last_log = now
            time.sleep(0.5)

        self.dll.CLIENT_StopDownload(c_longlong(handle))

        if not output_path.is_file() or output_path.stat().st_size < 1000:
            raise RuntimeError(
                "File rỗng — không có ghi hình trong khoảng thời gian, hoặc channel sai."
            )
        return output_path
