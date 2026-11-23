# OmniGrab

OmniGrab is an open-source Windows desktop application for downloading video and audio through a native, local interface.

## Download

Get the first release from the [GitHub Releases page](https://github.com/franzney11/OmniGrab/releases/latest):

- **Windows installer:** `OmniGrab-Setup.exe`
- **Portable version:** `OmniGrab-Portable.zip`

The installer creates Start menu and desktop shortcuts. The portable ZIP can be extracted and run without installation.

## Features

- Video and audio download modes.
- Resolution and audio bitrate selection.
- Download queue with progress, speed, file size, and estimated time.
- Download-only processing on the local machine.
- Local download and application data; no persistent link history.
- Native desktop window powered by WebView2.
- FastAPI local server with `yt-dlp` and FFmpeg.

## Free and PRO Rules

| Request | Result |
|---|---|
| Resolution equal to or below the source maximum | Free |
| Resolution above the source maximum | PRO: FFmpeg upscale; consumes one simulated credit |
| MP3 at 128, 192, 256, or 320 kbps | Free |

Rules are enforced by the local server, not only by the interface. The current `MockProvider` does not process real payments.

## Requirements

- Windows 10 or Windows 11 x64.
- Microsoft Edge WebView2 Runtime.
- Internet access for downloading media.

OmniGrab bundles Python dependencies and FFmpeg. Python does not need to be installed separately.

Windows 11 commonly includes WebView2. If it is missing on Windows 10 or another supported PC, install the official [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/).

Because the release is unsigned, Windows SmartScreen or an antivirus may display an unknown-publisher warning or false positive.

## Project Structure

```text
OmniGrab/
├── .github/workflows/build-windows.yml
├── backend/
│   ├── main.py
│   └── requirements.txt
├── frontend/
│   └── index.html
├── installer/
│   └── OmniGrab.iss
├── desktop_main.py
├── requirements-desktop.txt
├── LICENSE
└── README.md
```

## Build from Source

Requirements:

- Windows 10 or 11 x64.
- Python 3.10 or newer.
- Microsoft Edge WebView2.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-desktop.txt
python desktop_main.py
```

To build the Windows distribution:

```powershell
python -m PyInstaller --noconfirm --clean --windowed --onedir `
  --name OmniGrab `
  --collect-all yt_dlp `
  --collect-all imageio_ffmpeg `
  --collect-all webview `
  --collect-all uvicorn `
  --collect-all fastapi `
  --collect-all starlette `
  --collect-all pydantic `
  --add-data "frontend;frontend" `
  desktop_main.py
```

Compiled releases are available in GitHub Releases. Source builds should use GitHub Actions or a local Inno Setup installation for the installer.

## Local File Locations

- Finished downloads: `Downloads\OmniGrab`
- Application data and credits: `%LOCALAPPDATA%\OmniGrab\data`
- Temporary files: `%LOCALAPPDATA%\OmniGrab\temp`

The download queue exists only while the application is running.

## Download Folder

Open the **Downloads** tab to choose a destination folder.

- **Elegir carpeta** selects the destination for the next download only.
- **Descargar siempre aquí** saves the selected folder as the persistent default.
- Without a configured default, OmniGrab uses `Downloads\OmniGrab`.

## API

- `POST /api/analyze` - Analyze a URL.
- `POST /api/download` - Start a download job.
- `GET /api/jobs` - Read the in-memory download queue.
- `POST /api/open/{job_id}` - Open the downloaded file or output folder.
- `GET /api/health` - Local health check.

## License

OmniGrab is licensed under the [MIT License](LICENSE).

## Responsible Use

Only download content you have permission to save and comply with the terms of each content platform. The upscale feature uses Lanczos plus unsharp processing; it does not generate or recover real detail.
