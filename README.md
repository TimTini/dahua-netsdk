# dahua-netsdk

Download Dahua playback clips via NetSDK. **All runtime files live under this repo**; missing pieces are installed by `bootstrap.py`.

## Layout (dynamic, under repo root)

| Path | Role |
|------|------|
| `.venv/` | Python env (created by bootstrap) |
| `vendor/sdk/` | `dhnetsdk.dll` (+ deps, synced automatically) |
| `vendor/bin/` | `ffmpeg` (copied or installed by bootstrap) |
| `downloads/` | Clips `.dav` / `.mp4` |
| `.cache/` | SmartPSS install path cache, temp |
| `paths.py` | Path helpers (repo-relative only) |
| `bootstrap.py` | Create venv, sync SDK, install ffmpeg |

No hardcoded absolute or machine-specific paths in code.

## First run

```powershell
cd <clone-dir>
python bootstrap.py
.\.venv\Scripts\Activate.ps1
```

Bootstrap will:

1. Create `.venv` and `pip install -r requirements.txt`
2. Find SmartPSS on the machine (registry / PATH / Program Files env vars) and copy DLLs → `vendor/sdk/`
3. Put `ffmpeg` in `vendor/bin/`

Optional `config.local.env` (copy from `config.local.env.example`) for secrets and **relative** overrides.

## CLI-only Linux setup

Nếu chỉ cần `playback_clip.py --serial ...` thì không cần SmartPSS, GUI, hay NetSDK DLL.

```bash
cd /path/to/dahua-netsdk
python3 -m venv .venv
. .venv/bin/activate
python -m pip install cryptography==41.0.7 xmltodict==0.13.0
# cần ffmpeg/ffprobe trong PATH, ví dụ Debian/Ubuntu:
# sudo apt-get install -y ffmpeg
```

## Download clip CLI-only (không cần SmartPSS GUI)

Cách nhẹ để lấy record qua P2P: script tự mở `dh-p2p` local tunnel rồi tải RTSP playback bằng `ffmpeg`.

```powershell
$env:DAHUA_SERIAL = "..."
$env:DAHUA_PASS = "..."
python playback_clip.py --serial $env:DAHUA_SERIAL -p $env:DAHUA_PASS --channel 2 --minutes-ago 1 --duration-sec 60
```

Output: `downloads/clip_ch2_<timestamp>.mp4`.

Tuỳ chọn hay dùng:

```powershell
# đổi cloud nếu thiết bị đi qua Dolynk
python playback_clip.py --serial $env:DAHUA_SERIAL -p $env:DAHUA_PASS --p2p-server dolynk --channel 2

# nếu đang ở cùng LAN, bỏ P2P và lấy thẳng RTSP
python playback_clip.py --mode direct --host <camera-ip> -p $env:DAHUA_PASS --channel 2
```

## Download clip qua SmartPSS/NetSDK legacy

Chỉ cần nếu muốn dùng NetSDK Windows hoặc SmartPSS đang mở:

```powershell
.\scripts\ensure.ps1
$env:DAHUA_PASS = "..."
$port = .\scripts\get_smartpss_port.ps1   # SmartPSS must be running
python netsdk_download.py -p $env:DAHUA_PASS --ip 127.0.0.1 --port $port --channel-ui 2 --mp4
```

Output: `downloads/clip_ch2_<timestamp>.dav` and `.mp4`.

## Channel

| UI cam | `--channel-sdk` |
|--------|-----------------|
| 1 | 0 |
| 2 | 1 |

## Official SDK

https://depp.dahuasecurity.com/integration/guide/download/SDK
