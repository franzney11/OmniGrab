"""OmniGrab - backend local (FastAPI + yt-dlp + FFmpeg).

Reglas de negocio (se validan SIEMPRE en el servidor, nunca se confía en el cliente):
  - altura pedida <= resolución nativa máxima  -> GRATIS
  - altura pedida  > resolución nativa máxima  -> PRO (reescalado, cuesta 1 crédito)
  - solo audio (.mp3, 128/192/256/320 kbps)    -> GRATIS
Modos de /api/download: video_audio | video_only (sin audio) | audio_only
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Recursos de solo lectura (también funciona dentro del paquete PyInstaller).
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
FRONTEND = RESOURCE_ROOT / "frontend"
APP_VERSION = "1.0.0"
for version_path in (
    RESOURCE_ROOT / "version.txt",
    Path(getattr(sys, "_MEIPASS", RESOURCE_ROOT)) / "version.txt",
):
    try:
        if version_path.is_file():
            APP_VERSION = version_path.read_text(encoding="utf-8").strip() or APP_VERSION
            break
    except OSError:
        pass
# Los datos y temporales nunca se escriben dentro de Program Files ni del bundle.
if sys.platform == "win32":
    USER_DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OmniGrab"
else:
    USER_DATA = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "OmniGrab"
DOWNLOADS = USER_DATA / "temp"
DATA = USER_DATA / "data"
# Carpeta final de las descargas, visible desde la pestaña Descargas.
OUTPUT = Path(os.environ.get("OMNIGRAB_OUT") or (Path.home() / "Downloads" / "OmniGrab"))
for d in (DOWNLOADS, DATA):
    d.mkdir(parents=True, exist_ok=True)

LADDER = [144, 240, 360, 480, 720, 1080, 1440, 2160]
AUDIO_BITRATES = [128, 192, 256, 320]
MODES = ("video_audio", "video_only", "audio_only")
LABELS = {1440: "1440p (2K)", 2160: "2160p (4K)"}
CACHE_TTL = 600  # segundos que se reutiliza un análisis


# --------------------------------------------------------------------------
# FFmpeg: el del sistema o, si no hay, el que trae imageio-ffmpeg
# --------------------------------------------------------------------------
def find_ffmpeg():
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = find_ffmpeg()


# --------------------------------------------------------------------------
# Pagos: interfaz + proveedor simulado. Para Stripe/LemonSqueezy basta con
# crear otra clase con el mismo método y cambiar PAYMENT_PROVIDER.
# --------------------------------------------------------------------------
PACKS = {
    "pack_1": {"credits": 1, "price": 0.99, "label": "1 reescalado"},
    "pack_5": {"credits": 5, "price": 3.99, "label": "5 reescalados"},
    "pack_20": {"credits": 20, "price": 12.99, "label": "20 reescalados"},
}


class PaymentProvider:
    def charge(self, pack_id: str, payload: dict) -> bool:
        raise NotImplementedError


class MockProvider(PaymentProvider):
    """Simula un cobro: acepta cualquier tarjeta que no empiece por 0000."""

    def charge(self, pack_id, payload):
        time.sleep(1.0)  # latencia de pasarela
        return not str(payload.get("card", "")).replace(" ", "").startswith("0000")


PAYMENT_PROVIDER: PaymentProvider = MockProvider()

CREDITS_FILE = DATA / "credits.json"
_credits_lock = threading.Lock()
SETTINGS_FILE = DATA / "settings.json"
_settings_lock = threading.Lock()


def get_default_output_dir() -> Path:
    configured = os.environ.get("OMNIGRAB_OUT")
    if configured:
        return Path(configured).expanduser()
    with _settings_lock:
        try:
            value = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("default_output_dir")
            return Path(value).expanduser() if value else OUTPUT
        except Exception:
            return OUTPUT


def set_default_output_dir(folder: str) -> Path:
    if not folder.strip():
        raise HTTPException(400, "La carpeta no puede estar vacía.")
    path = Path(folder).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(400, f"No se pudo crear la carpeta: {e}") from e
    if not path.is_dir():
        raise HTTPException(400, "La ruta seleccionada no es una carpeta.")
    with _settings_lock:
        SETTINGS_FILE.write_text(json.dumps({"default_output_dir": str(path)}), encoding="utf-8")
    return path


def resolve_output_dir(folder: str | None) -> Path:
    if folder:
        path = Path(folder).expanduser().resolve()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"No se pudo crear la carpeta: {e}") from e
        if not path.is_dir():
            raise HTTPException(400, "La ruta seleccionada no es una carpeta.")
        return path
    return get_default_output_dir()


def _credits_read() -> int:
    try:
        return int(json.loads(CREDITS_FILE.read_text())["credits"])
    except Exception:
        return 0


def credits_get() -> int:
    with _credits_lock:
        return _credits_read()


def credits_add(n: int) -> int:
    with _credits_lock:
        total = _credits_read() + n
        CREDITS_FILE.write_text(json.dumps({"credits": total}))
        return total


def credits_take(n: int) -> bool:
    with _credits_lock:
        cur = _credits_read()
        if cur < n:
            return False
        CREDITS_FILE.write_text(json.dumps({"credits": cur - n}))
        return True


# --------------------------------------------------------------------------
# Análisis de metadatos
# --------------------------------------------------------------------------
_cache: dict[str, tuple[float, dict]] = {}


def validate_url(url: str) -> str:
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise HTTPException(400, "Introduce una URL http(s) válida.")
    return url


def short_side(fmt: dict) -> int:
    w, h = fmt.get("width"), fmt.get("height")
    if w and h:
        return min(w, h)
    return h or 0


def est_bytes(f: dict, duration: float):
    """(bytes, es_exacto). Exacto solo si el sitio informa del tamaño real."""
    if f.get("filesize"):
        return int(f["filesize"]), True
    if f.get("filesize_approx"):
        return int(f["filesize_approx"]), False
    if f.get("tbr") and duration:
        return int(f["tbr"] * 125 * duration), False
    return None, False


def estimate(meta: dict, height: int, with_audio: bool):
    """Tamaño estimado de una opción de vídeo: {"bytes", "exact"} o None."""
    vl = meta["vlist"]
    if not vl:
        return None
    native = meta["native_max"]
    upscale = bool(height and native and height > native)
    cands = vl if (not height or upscale) else [v for v in vl if v["side"] <= height]
    if not cands:
        return None
    best = max(cands, key=lambda v: (v["side"], v["tbr"]))
    audio = meta["audio_bytes"]
    size, exact = best["size"], best["exact"]
    if best["muxed"]:  # el stream ya incluye audio
        if not with_audio:
            size, exact = max(0, size - (audio or 0)), False
    elif with_audio:
        size += audio or 0
        exact = exact and audio is not None and meta["audio_exact"]
    if upscale:  # re-codificación a mayor resolución: solo una aproximación
        video_part = size - ((audio or 0) if with_audio else 0)
        size, exact = int(video_part * (height / native) ** 2) + ((audio or 0) if with_audio else 0), False
    return {"bytes": int(size), "exact": exact}


def probe(url: str) -> dict:
    cached = _cache.get(url)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(422, re.sub(r"\x1b\[[0-9;]*m", "", str(e))[:300])
    if info.get("entries"):  # playlist: nos quedamos con el primero
        info = next(iter(info["entries"]), None) or info

    vfmts = [f for f in info.get("formats", []) if f.get("vcodec") not in (None, "none")]
    sides = sorted({short_side(f) for f in vfmts if short_side(f)})
    portrait = any(f.get("width") and f.get("height") and f["width"] < f["height"] for f in vfmts)
    if not sides and info.get("height"):
        sides = [short_side(info)]
    native_max = max(sides) if sides else 0
    abrs = [
        f.get("abr") or f.get("tbr") or 0
        for f in info.get("formats", [])
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    audio_kbps = int(max(abrs)) if abrs else 0

    # Tamaños para la estimación previa a la descarga
    duration = info.get("duration") or 0
    audio_fmts = [f for f in info.get("formats", [])
                  if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    best_a = max(audio_fmts, key=lambda f: f.get("abr") or f.get("tbr") or 0, default=None)
    a_bytes, a_exact = est_bytes(best_a, duration) if best_a else (None, False)
    vlist = []
    for f in vfmts:
        s, ex = est_bytes(f, duration)
        if s and short_side(f):
            vlist.append({"side": short_side(f), "size": s, "exact": ex, "tbr": f.get("tbr") or 0,
                          "muxed": f.get("acodec") not in (None, "none")})

    meta = {
        "url": url,
        "title": info.get("title") or "video",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration") or 0,
        "platform": info.get("extractor_key") or info.get("extractor") or "Web",
        "native_max": native_max,
        "portrait": portrait,
        "audio_kbps": audio_kbps,
        "min_side": min(sides) if sides else 0,
        "vlist": vlist,
        "audio_bytes": a_bytes,
        "audio_exact": a_exact,
    }
    _cache[url] = (time.time(), meta)
    return meta


def build_options(meta: dict) -> list[dict]:
    native_max = meta["native_max"]
    if native_max == 0:  # el sitio no informa de resoluciones
        opts = [{"height": 0, "label": "Mejor disponible", "tier": "free"}]
    else:
        low = meta.get("min_side", 0)  # no ofrecer resoluciones por debajo de la más baja real
        heights = {h for h in LADDER if low <= h <= native_max} | {native_max}
        opts = [{"height": h, "label": LABELS.get(h, f"{h}p"), "tier": "free"} for h in sorted(heights)]
        opts += [
            {"height": h, "label": LABELS.get(h, f"{h}p"), "tier": "pro"}
            for h in LADDER
            if h > native_max
        ]
    for o in opts:
        o["size_va"] = estimate(meta, o["height"], True)
        o["size_vo"] = estimate(meta, o["height"], False)
    return opts


# --------------------------------------------------------------------------
# Trabajos de descarga
# --------------------------------------------------------------------------
jobs: dict[str, dict] = {}
ACTIVE = ("queued", "downloading", "processing", "upscaling")
_slots = threading.BoundedSemaphore(2)  # descargas simultáneas; el resto espera en cola


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    i = 1
    while True:
        cand = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not cand.exists():
            return cand
        i += 1


def describe(mode, height, bitrate, upscale) -> str:
    if mode == "audio_only":
        return f"MP3 {bitrate} kbps"
    q = f"{height}p" if height else "mejor calidad"
    what = "sin audio" if mode == "video_only" else "con audio"
    return f"{q} · {what}" + (" · PRO" if upscale else "")


def reveal(path):
    """Abre el explorador de archivos (con el archivo seleccionado si existe)."""
    if sys.platform == "win32":
        if path:
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            os.startfile(str(OUTPUT))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path else ["open", str(OUTPUT)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path else OUTPUT)])


def clean_old_files(max_age=24 * 3600):
    now = time.time()
    for f in DOWNLOADS.iterdir():
        if f.is_file() and f.name != ".gitkeep" and now - f.stat().st_mtime > max_age:
            f.unlink(missing_ok=True)


def ffmpeg_upscale(src: Path, dst: Path, target: int, portrait: bool, duration: float, job: dict, audio: bool = True):
    scale = f"scale={target}:-2" if portrait else f"scale=-2:{target}"
    vf = f"{scale}:flags=lanczos,unsharp=5:5:0.8:3:3:0.4"
    cmd = [
        FFMPEG, "-y", "-i", str(src), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        *(["-c:a", "aac", "-b:a", "192k"] if audio else ["-an"]),
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(dst),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    for line in proc.stdout:
        if line.startswith("out_time_ms=") and duration:
            try:
                secs = int(line.split("=")[1]) / 1_000_000
                job["progress"] = min(99.0, round(secs / duration * 100, 1))
            except ValueError:
                pass
    if proc.wait() != 0:
        raise RuntimeError("FFmpeg falló al reescalar el vídeo.")


def fmt_speed(bps) -> str:
    if not bps:
        return ""
    return f"{bps / 1048576:.2f} MiB/s" if bps >= 1048576 else f"{bps / 1024:.0f} KiB/s"


def fmt_eta(secs) -> str:
    if secs is None:
        return ""
    secs = int(secs)
    h, m, s = secs // 3600, secs % 3600 // 60, secs % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def strip_audio(src: Path, dst: Path):
    """Quita la pista de audio sin recodificar el vídeo."""
    cmd = [FFMPEG, "-y", "-i", str(src), "-c", "copy", "-an", "-movflags", "+faststart", str(dst)]
    if subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        raise RuntimeError("FFmpeg falló al quitar el audio.")


def run_job(job_id: str, meta: dict, height: int, mode: str, upscale: bool, bitrate: int = 192, output_dir: Path | None = None):
    with _slots:  # si ya hay dos en marcha, esta espera en estado 'En cola'
        _run_job(job_id, meta, height, mode, upscale, bitrate, output_dir)


def _run_job(job_id: str, meta: dict, height: int, mode: str, upscale: bool, bitrate: int = 192, output_dir: Path | None = None):
    job = jobs[job_id]
    try:
        def hook(d):
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                job["progress"] = round(d["downloaded_bytes"] / total * 100, 1) if total else 0
                job["done_bytes"] = d.get("downloaded_bytes") or 0
                job["total_bytes"] = total
                # Valores numéricos (los *_str de yt-dlp traen códigos de color ANSI)
                job["speed"] = fmt_speed(d.get("speed"))
                job["eta"] = fmt_eta(d.get("eta"))

        def pphook(d):  # unión de streams / conversión a mp3
            if d["status"] == "started":
                job.update(stage="processing", progress=100)

        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": str(DOWNLOADS / f"{job_id}.%(ext)s"),
            "progress_hooks": [hook],
            "postprocessor_hooks": [pphook],
            "ffmpeg_location": FFMPEG,
            "concurrent_fragment_downloads": 4,  # acelera HLS/DASH
        }
        if mode == "audio_only":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(bitrate)},
                {"key": "FFmpegMetadata", "add_metadata": True},  # título, artista, etc.
            ]
        else:
            key = "width" if meta["portrait"] else "height"
            with_audio = mode == "video_audio"
            if height and not upscale:
                sel = (f"bv*[{key}<={height}]+ba/b[{key}<={height}]/b" if with_audio
                       else f"bv*[{key}<={height}]/b[{key}<={height}]/b")
            else:  # mejor calidad nativa (también como origen del reescalado)
                sel = "bv*+ba/b" if with_audio else "bv*/b"
            opts.update(
                format=sel,
                merge_output_format="mp4",
                format_sort=["res", "vcodec:h264", "acodec:m4a"],
            )

        job.update(stage="downloading", progress=0)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([meta["url"]])

        wanted = ".mp3" if mode == "audio_only" else ".mp4"
        files = [f for f in DOWNLOADS.glob(f"{job_id}.*") if f.suffix not in (".part", ".ytdl")]
        final = next((f for f in files if f.suffix == wanted), files[0] if files else None)
        if not final:
            raise RuntimeError("No se encontró el archivo descargado.")

        if upscale:
            job.update(stage="upscaling", progress=0)
            out = DOWNLOADS / f"{job_id}_up.mp4"
            ffmpeg_upscale(final, out, height, meta["portrait"], meta["duration"], job,
                           audio=(mode == "video_audio"))
            final.unlink(missing_ok=True)
            final = out
        elif mode == "video_only":
            job.update(stage="processing", progress=100)
            out = DOWNLOADS / f"{job_id}_na.mp4"
            strip_audio(final, out)
            final.unlink(missing_ok=True)
            final = out

        safe = re.sub(r'[\\/:*?"<>|]+', "_", meta["title"]).strip()[:120] or "video"
        if mode == "audio_only":
            suffix = f" [{bitrate}kbps]"
        elif height:
            suffix = f" [{height}p{' sin audio' if mode == 'video_only' else ''}]"
        else:
            suffix = " [sin audio]" if mode == "video_only" else ""
        destination = output_dir or get_default_output_dir()
        destination.mkdir(parents=True, exist_ok=True)
        dest = unique_path(destination / f"{safe}{suffix}{final.suffix}")
        shutil.move(str(final), dest)
        job.update(stage="done", progress=100, path=str(dest), filename=dest.name)
    except Exception as e:  # noqa: BLE001
        if upscale:
            credits_add(1)  # reembolso si falla un trabajo de pago
        job.update(stage="error", message=re.sub(r"\x1b\[[0-9;]*m", "", str(e))[:300])


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
app = FastAPI(title="OmniGrab")

RELEASES_LATEST = "https://github.com/franzney11/OmniGrab/releases/latest"
UPDATE_LOCK = threading.Lock()
UPDATE_STATUS = {
    "checking": False,
    "available": False,
    "downloading": False,
    "installing": False,
    "error": "",
    "current_version": APP_VERSION,
    "latest_version": "",
    "release_url": "",
    "notes": "",
    "asset_name": "",
}


def version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value.strip().lstrip("vV"))
    parts = tuple(int(number) for number in numbers) if numbers else (0,)
    return parts + (0,) * max(0, 3 - len(parts))


def is_portable_install() -> bool:
    if not getattr(sys, "frozen", False):
        return False
    executable = str(Path(sys.executable).resolve()).lower()
    program_files = str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")).resolve()).lower()
    program_files_x86 = str(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")).resolve()).lower()
    return not executable.startswith(program_files) and not executable.startswith(program_files_x86)


def check_latest_release() -> dict:
    request = urllib.request.Request(
        RELEASES_LATEST,
        headers={"User-Agent": "OmniGrab-Updater"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        final_url = str(response.url or "")
        tag = final_url.rstrip("/").rsplit("/", 1)[-1].strip()
    latest = tag.lstrip("vV")
    if not latest or not re.fullmatch(r"\d+(?:\.\d+)*", latest):
        raise RuntimeError("No se pudo determinar la última versión.")

    portable = is_portable_install()
    preferred = "OmniGrab-Portable.zip" if portable else "OmniGrab-Setup.exe"
    asset_name = preferred
    download_url = f"https://github.com/franzney11/OmniGrab/releases/latest/download/{asset_name}"
    release_url = final_url

    return {
        "current_version": APP_VERSION,
        "latest_version": latest,
        "available": bool(version_tuple(latest) > version_tuple(APP_VERSION)),
        "release_url": release_url,
        "notes": "",
        "asset_name": asset_name,
        "download_url": download_url,
        "portable": portable,
    }


def trusted_github_download(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "github.com" or host.endswith(".github.com") or host.endswith(".githubusercontent.com")


def download_update_asset(download_url: str, asset_name: str) -> Path:
    if not trusted_github_download(download_url):
        raise RuntimeError("La descarga no proviene de GitHub.")
    destination_dir = Path(tempfile.gettempdir()) / "OmniGrab-Update"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / asset_name
    request = urllib.request.Request(download_url, headers={"User-Agent": "OmniGrab-Updater"})
    with urllib.request.urlopen(request, timeout=60) as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)
    return destination


def safe_extract_zip(package: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(package) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError("El paquete portable contiene una ruta no segura.")
        archive.extractall(destination)


def launch_installer(installer: Path) -> None:
    subprocess.Popen(
        [
            str(installer),
            "/SILENT",
            "/CLOSEAPPLICATIONS",
            "/FORCECLOSEAPPLICATIONS",
            "/NORESTART",
        ],
        shell=False,
    )


def launch_portable_update(package: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Solo la versión portable compilada puede actualizarse desde aquí.")

    app_dir = Path(sys.executable).resolve().parent
    extract_dir = Path(tempfile.gettempdir()) / "OmniGrab-Update" / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    safe_extract_zip(package, extract_dir)

    executable = next(extract_dir.rglob("OmniGrab.exe"), None)
    if not executable:
        raise RuntimeError("El paquete portable no contiene OmniGrab.exe.")

    source_dir = executable.parent
    script = Path(tempfile.gettempdir()) / "OmniGrab-Update" / "replace-portable.cmd"
    backup = app_dir.parent / f"{app_dir.name}-backup-{uuid.uuid4().hex}"
    script.write_text(
        "@echo off\r\n"
        f'set "parent_pid={os.getpid()}"\r\n'
        "set attempt=0\r\n"
        ":WAITOLD\r\n"
        'tasklist /FI "PID eq %parent_pid%" /NH 2>nul | find /I "OmniGrab.exe" >nul\r\n'
        "if errorlevel 1 goto READYOLD\r\n"
        "set /a attempt+=1\r\n"
        "if %attempt% GEQ 30 exit /b 9\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        "goto WAITOLD\r\n"
        ":READYOLD\r\n"
        "set attempt=0\r\n"
        ":MOVEOLD\r\n"
        f'move /Y "{app_dir}" "{backup}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto MOVENEW\r\n"
        "set /a attempt+=1\r\n"
        "if %attempt% GEQ 12 exit /b 10\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        "goto MOVEOLD\r\n"
        "set attempt=0\r\n"
        ":MOVENEW\r\n"
        f'move /Y "{source_dir}" "{app_dir}" >nul 2>&1\r\n'
        "if not errorlevel 1 goto STARTNEW\r\n"
        "set /a attempt+=1\r\n"
        "if %attempt% GEQ 12 (\r\n"
        f'  move /Y "{backup}" "{app_dir}" >nul 2>&1\r\n'
        "  exit /b 11\r\n"
        ")\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        "goto MOVENEW\r\n"
        ":STARTNEW\r\n"
        f'start "" "{app_dir}\\OmniGrab.exe"\r\n',
        encoding="utf-8",
    )
    subprocess.Popen(
        [
            "cmd.exe",
            "/d",
            "/c",
            str(script),
        ],
        shell=False,
    )


def run_update(download_url: str, asset_name: str) -> None:
    with UPDATE_LOCK:
        UPDATE_STATUS.update(downloading=True, installing=False, error="")
    try:
        package = download_update_asset(download_url, asset_name)
        with UPDATE_LOCK:
            UPDATE_STATUS.update(downloading=False, installing=True)
        if asset_name == "OmniGrab-Setup.exe":
            launch_installer(package)
        elif asset_name == "OmniGrab-Portable.zip":
            launch_portable_update(package)
        else:
            raise RuntimeError("Formato de actualización no reconocido.")
    except Exception as exc:
        with UPDATE_LOCK:
            UPDATE_STATUS.update(downloading=False, installing=False, error=str(exc)[:300])


@app.on_event("startup")
def _startup():
    clean_old_files()
    if not FFMPEG:
        print("[!] FFmpeg no encontrado: instala ffmpeg o `pip install imageio-ffmpeg`.")


class AnalyzeIn(BaseModel):
    url: str


class DownloadIn(BaseModel):
    url: str
    height: int = 0
    mode: str = "video_audio"  # video_audio | video_only | audio_only
    audio_bitrate: int = 192   # solo para audio_only: 128 | 192 | 256 | 320
    output_dir: str | None = None


class FolderSettingsIn(BaseModel):
    default_output_dir: str


class CheckoutIn(BaseModel):
    pack_id: str
    card: str = ""


@app.get("/api/health")
def health():
    return {"ffmpeg": bool(FFMPEG), "credits": credits_get()}


@app.get("/api/update/check")
def update_check():
    with UPDATE_LOCK:
        UPDATE_STATUS["checking"] = True
        UPDATE_STATUS["error"] = ""
    try:
        result = check_latest_release()
        with UPDATE_LOCK:
            UPDATE_STATUS.update(
                checking=False,
                available=result["available"],
                latest_version=result["latest_version"],
                release_url=result["release_url"],
                notes=result["notes"],
                asset_name=result["asset_name"],
            )
        return result
    except Exception as exc:
        with UPDATE_LOCK:
            UPDATE_STATUS.update(checking=False, available=False, error=str(exc)[:300])
        raise HTTPException(502, f"No se pudo comprobar GitHub Releases: {str(exc)[:200]}") from exc


@app.post("/api/update/download")
def update_download():
    try:
        result = check_latest_release()
    except Exception as exc:
        raise HTTPException(502, f"No se pudo comprobar GitHub Releases: {str(exc)[:200]}") from exc
    if not result["available"] or not result["download_url"]:
        raise HTTPException(409, "No hay una actualización disponible.")

    with UPDATE_LOCK:
        if UPDATE_STATUS["downloading"] or UPDATE_STATUS["installing"]:
            return {"started": False}

    threading.Thread(
        target=run_update,
        args=(result["download_url"], result["asset_name"]),
        daemon=True,
    ).start()
    return {"started": True}


@app.get("/api/update/status")
def update_status():
    with UPDATE_LOCK:
        return dict(UPDATE_STATUS)


@app.post("/api/analyze")
async def analyze(body: AnalyzeIn):
    url = validate_url(body.url)
    meta = await asyncio.to_thread(probe, url)
    return {
        **{k: meta[k] for k in ("title", "thumbnail", "duration", "platform", "native_max", "portrait", "audio_kbps")},
        "options": build_options(meta),
        "audio_options": [
            {"bitrate": k, "bytes": int(k * 125 * meta["duration"]) if meta["duration"] else None}
            for k in AUDIO_BITRATES
        ],
        "credits": credits_get(),
    }


@app.post("/api/download")
async def download(body: DownloadIn):
    if not FFMPEG:
        raise HTTPException(500, "FFmpeg no está disponible.")
    if body.mode not in MODES:
        raise HTTPException(400, "Modo inválido.")
    audio_only = body.mode == "audio_only"
    if audio_only and body.audio_bitrate not in AUDIO_BITRATES:
        raise HTTPException(400, "Bitrate no soportado.")
    url = validate_url(body.url)
    meta = await asyncio.to_thread(probe, url)
    output_dir = resolve_output_dir(body.output_dir)

    req = (body.mode, body.height, body.audio_bitrate)
    # Solo una descarga activa por enlace: evita que se puedan iniciar varias
    # calidades/modos del mismo vídeo mientras la interfaz está bloqueada.
    if any(j["url"] == url and j["stage"] in ACTIVE for j in list(jobs.values())):
        raise HTTPException(409, "Ya hay una descarga de este enlace en curso.")

    # Validación Gratis vs Upscale (autoridad = servidor)
    if not audio_only and body.height and body.height not in LADDER and body.height > meta["native_max"]:
        raise HTTPException(400, "Resolución no soportada.")
    upscale = (not audio_only) and body.height > meta["native_max"] > 0  # audio: siempre gratis
    if upscale and not credits_take(1):
        raise HTTPException(402, "Necesitas 1 crédito PRO para reescalar.")

    job_id = uuid.uuid4().hex
    jobs[job_id] = {"id": job_id, "url": url, "req": req, "created": time.time(),
                    "title": meta["title"], "label": describe(body.mode, body.height, body.audio_bitrate, upscale),
                    "stage": "queued", "progress": 0, "speed": "", "eta": "", "message": "", "filename": "",
                    "path": "", "upscale": upscale, "done_bytes": 0, "total_bytes": 0,
                    "output_dir": str(output_dir)}
    threading.Thread(
        target=run_job, args=(job_id, meta, body.height, body.mode, upscale, body.audio_bitrate, output_dir), daemon=True
    ).start()
    return {"job_id": job_id, "upscale": upscale, "credits": credits_get()}


@app.get("/api/progress/{job_id}")
async def progress(job_id: str):
    async def stream():
        last = None
        while True:
            job = jobs.get(job_id)
            if not job:
                yield 'data: {"stage":"error","message":"Trabajo no encontrado"}\n\n'
                return
            snap = json.dumps({k: job[k] for k in ("stage", "progress", "speed", "eta", "message", "filename", "upscale", "done_bytes", "total_bytes")})
            if snap != last:
                yield f"data: {snap}\n\n"
                last = snap
            if job["stage"] in ("done", "error"):
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(stream(), media_type="text/event-stream")


JOB_FIELDS = ("id", "url", "title", "label", "stage", "progress", "speed", "eta", "message",
              "filename", "upscale", "done_bytes", "total_bytes", "created", "output_dir")


@app.get("/api/jobs")
def list_jobs():
    rows = sorted(list(jobs.values()), key=lambda j: j["created"], reverse=True)
    return [{k: j.get(k) for k in JOB_FIELDS} for j in rows]


@app.post("/api/open/{job_id}")
def open_folder(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = Path(job["path"]) if job.get("path") else None
    reveal(path if path and path.exists() else None)
    return {"ok": True}


@app.get("/api/settings/folders")
def get_folder_settings():
    return {"default_output_dir": str(get_default_output_dir())}


@app.post("/api/settings/folders")
def set_folder_settings(body: FolderSettingsIn):
    path = set_default_output_dir(body.default_output_dir)
    return {"default_output_dir": str(path)}


@app.get("/api/credits")
def credits():
    return {"credits": credits_get(), "packs": PACKS}


@app.post("/api/checkout")
async def checkout(body: CheckoutIn):
    pack = PACKS.get(body.pack_id)
    if not pack:
        raise HTTPException(400, "Pack desconocido.")
    ok = await asyncio.to_thread(PAYMENT_PROVIDER.charge, body.pack_id, {"card": body.card})
    if not ok:
        raise HTTPException(402, "Pago rechazado (simulado).")
    return {"credits": credits_add(pack["credits"])}


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
