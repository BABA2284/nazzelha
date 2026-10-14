from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMP_ROOT = Path(os.getenv("DOWNLOAD_DIR", "/tmp/nazzelha-downloads"))
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_MB", "500")) * 1024 * 1024
MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_HOURS", "3")) * 3600
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_MINUTES", "20")) * 60
MAX_ACTIVE_JOBS_PER_IP = int(os.getenv("MAX_ACTIVE_JOBS_PER_IP", "2"))
MAX_ACTIVE_JOBS_GLOBAL = int(os.getenv("MAX_ACTIVE_JOBS_GLOBAL", "6"))

ALLOWED_DOMAINS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "instagram.com", "instagr.am", "facebook.com", "fb.watch", "tiktok.com",
)

logger = logging.getLogger("nazzelha")

app = FastAPI(title="Nazzelha - نزّلها", version="1.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
lock = threading.RLock()
jobs: dict[str, dict] = {}
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nazzelha-download")


class URLRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class DownloadRequest(URLRequest):
    quality: str = Field(default="best", pattern="^(best|1080|720|480|audio)$")


class AuthRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


def client_ip(request: Request) -> str:
    # Behind a trusted hosting proxy, request.client.host is the proxy address.
    # Use the first forwarded value only when the app is deployed behind that proxy.
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded[:64] if forwarded else (request.client.host if request.client else "unknown")


def require_auth(request: Request) -> None:
    if not APP_PASSWORD:
        raise HTTPException(status_code=503, detail="إعداد كلمة مرور الأداة غير مكتمل على السيرفر.")
    if not hmac.compare_digest(request.headers.get("x-app-password", ""), APP_PASSWORD):
        raise HTTPException(status_code=401, detail="كلمة المرور غير صحيحة أو انتهت الجلسة.")


def validate_url(raw_url: str) -> str:
    url = raw_url.strip()
    if len(url) > 2048:
        raise HTTPException(status_code=400, detail="الرابط طويل بشكل غير متوقع.")
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise HTTPException(status_code=400, detail="استخدم رابط HTTPS كامل من المنصة.")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise HTTPException(status_code=400, detail="صيغة الرابط غير مقبولة.")
    host = parsed.hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise HTTPException(status_code=400, detail="روابط عناوين IP غير مسموح بها.")
    if not any(host == domain or host.endswith("." + domain) for domain in ALLOWED_DOMAINS):
        raise HTTPException(status_code=400, detail="المنصة غير مدعومة. استخدم YouTube أو Instagram أو Facebook أو TikTok.")
    return url


def platform_for(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host == "youtu.be" or host.endswith(("youtube.com", "youtube-nocookie.com")):
        return "YouTube"
    if host.endswith(("instagram.com", "instagr.am")):
        return "Instagram"
    if host.endswith("facebook.com") or host == "fb.watch":
        return "Facebook"
    if host.endswith("tiktok.com"):
        return "TikTok"
    return "رابط فيديو"


def ydl_base_options() -> dict:
    return {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "socket_timeout": 25, "retries": 2, "fragment_retries": 2,
        "cachedir": False, "ignoreerrors": False, "restrictfilenames": True,
        "trim_file_name": 120, "max_filesize": MAX_FILE_BYTES,
        "retries": 4, "fragment_retries": 4, "extractor_retries": 3,
        # Avoid rapid bursts that can trigger rate limits on public providers.
        "sleep_interval_requests": 1,
        "sleep_interval": 1,
        "max_sleep_interval": 3,
        "js_runtimes": {"deno": {}},
    }


def extract_info(url: str) -> dict:
    opts = ydl_base_options()
    opts.update({"skip_download": True, "extract_flat": False})
    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(url, download=False)
    if not isinstance(data, dict):
        raise ValueError("لم يتم العثور على بيانات فيديو صالحة.")
    duration = data.get("duration")
    if duration and duration > MAX_DURATION_SECONDS:
        raise ValueError(f"مدة الفيديو أطول من الحد المسموح ({MAX_DURATION_SECONDS // 3600} ساعات).")
    thumbnail = data.get("thumbnail") or ""
    if not thumbnail.startswith("https://"):
        thumbnail = ""
    return {
        "title": str(data.get("title") or "فيديو بدون عنوان")[:240],
        "thumbnail": thumbnail,
        "duration": int(duration) if isinstance(duration, (int, float)) and duration >= 0 else None,
        "uploader": str(data.get("uploader") or data.get("channel") or "")[:120],
        "platform": platform_for(url),
    }


def format_selector(quality: str) -> str:
    if quality == "audio":
        return "bestaudio/best"
    if quality == "best":
        return "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/best"
    height = int(quality)
    return f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/b[height<={height}][ext=mp4]/best[height<={height}]/best"


def progress_hook(job_id: str):
    def _hook(data: dict) -> None:
        with lock:
            job = jobs.get(job_id)
            if not job:
                return
            if data.get("status") == "downloading":
                downloaded = int(data.get("downloaded_bytes") or 0)
                total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
                if downloaded > MAX_FILE_BYTES:
                    raise yt_dlp.utils.DownloadError("حجم الملف أكبر من الحد المسموح.")
                job.update({
                    "state": "downloading", "downloaded_bytes": downloaded,
                    "total_bytes": total,
                    "progress": min(99, int(downloaded * 100 / total)) if total else None,
                    "message": "جاري تنزيل الفيديو...",
                })
            elif data.get("status") == "finished":
                job.update({"progress": 99, "message": "اكتمل التنزيل، جاري تجهيز الملف..."})
    return _hook


def find_result_file(folder: Path) -> Path:
    ignored = {".part", ".ytdl", ".json", ".jpg", ".jpeg", ".png", ".webp", ".description", ".vtt", ".srt", ".ass"}
    choices = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() not in ignored and not p.name.endswith(".part-Frag")]
    if not choices:
        raise ValueError("لم يتم العثور على ملف الفيديو الناتج.")
    return max(choices, key=lambda p: p.stat().st_size)


def perform_download(job_id: str, url: str, quality: str) -> None:
    with lock:
        job = jobs.get(job_id)
        if not job:
            return
        job.update({"state": "downloading", "message": "جاري الاتصال بالمنصة..."})
    folder = TEMP_ROOT / job_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        probe_opts = ydl_base_options()
        probe_opts.update({"skip_download": True, "extract_flat": False})
        with yt_dlp.YoutubeDL(probe_opts) as probe:
            metadata = probe.extract_info(url, download=False)
        if not isinstance(metadata, dict):
            raise ValueError("المنصة لم ترجع بيانات فيديو صالحة.")
        duration = metadata.get("duration")
        if duration and duration > MAX_DURATION_SECONDS:
            raise ValueError(f"مدة الفيديو أطول من الحد المسموح ({MAX_DURATION_SECONDS // 3600} ساعات).")
        with lock:
            if job_id in jobs:
                jobs[job_id].update({"title": str(metadata.get("title") or "فيديو")[:240], "message": "تم التعرف على الفيديو، جاري بدء التنزيل..."})
        opts = ydl_base_options()
        opts.update({
            "outtmpl": str(folder / "%(title).100s-%(id)s.%(ext)s"),
            "format": format_selector(quality), "merge_output_format": "mp4",
            "progress_hooks": [progress_hook(job_id)], "overwrites": True,
        })
        if quality == "audio":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
            opts["merge_output_format"] = "mp3"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        if not isinstance(info, dict):
            raise ValueError("المنصة لم ترجع ملف فيديو صالحًا.")
        if quality == "audio":
            audio_files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".mp3" and p.stat().st_size > 0]
            if not audio_files:
                raise ValueError("FFmpeg لم ينتج ملف MP3.")
            result_file = max(audio_files, key=lambda p: p.stat().st_size)
        else:
            result_file = find_result_file(folder)
        size = result_file.stat().st_size
        if size <= 0:
            raise ValueError("الملف الناتج فارغ.")
        if size > MAX_FILE_BYTES:
            raise ValueError(f"حجم الملف أكبر من الحد المسموح ({MAX_FILE_BYTES // (1024 * 1024)} ميجابايت).")
        with lock:
            if job_id in jobs:
                jobs[job_id].update({
                    "state": "completed", "progress": 100,
                    "message": "الفيديو جاهز للتنزيل على الآيفون.",
                    "filename": result_file.name, "filesize": size, "completed_at": time.time(),
                })
    except Exception as exc:
        logger.exception("Download failed for platform=%s quality=%s", platform_for(url), quality)
        message = str(exc)
        known = ("حجم الملف أكبر", "مدة الفيديو أطول", "الرابط غير متاح", "Private video", "This video is private", "login", "Sign in", "Unsupported URL", "لم يتم العثور", "تم حذف", "Video unavailable", "not a bot", "captcha", "PO Token", "verification")
        if not any(term.casefold() in message.casefold() for term in known):
            message = "تعذّر تنزيل الرابط. قد يكون خاصًا أو يحتاج تسجيل دخول أو أن المنصة غيّرت طريقة الوصول."
        elif "not a bot" in message.casefold() or "captcha" in message.casefold() or "po token" in message.casefold() or "verification" in message.casefold():
            message = "يوتيوب رفض طلب السيرفر باعتباره آليًا. جرّب لاحقًا؛ بعض الفيديوهات العامة لا يمكن استخراجها من بيئة الاستضافة الحالية دون تحقق إضافي."
        elif len(message) > 220:
            message = message[:220]
        with lock:
            if job_id in jobs:
                jobs[job_id].update({"state": "failed", "message": message, "completed_at": time.time()})
        shutil.rmtree(folder, ignore_errors=True)


def cleanup_expired() -> None:
    now = time.time()
    with lock:
        expired = [jid for jid, job in jobs.items() if job.get("state") in ("completed", "failed") and now - job.get("completed_at", job.get("created_at", now)) > JOB_TTL_SECONDS]
        for jid in expired:
            shutil.rmtree(TEMP_ROOT / jid, ignore_errors=True)
            jobs.pop(jid, None)


def public_job(job_id: str, job: dict) -> dict:
    payload = {
        "id": job_id, "state": job.get("state"), "progress": job.get("progress"),
        "downloaded_bytes": job.get("downloaded_bytes", 0), "total_bytes": job.get("total_bytes", 0),
        "message": job.get("message", ""), "title": job.get("title", ""), "filesize": job.get("filesize"),
    }
    if job.get("state") == "completed":
        payload["filename"] = job.get("filename", "video.mp4")
    return payload


@app.get("/", response_class=HTMLResponse)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


@app.get("/api/health")
def health():
    return {"ok": True, "app": "nazzelha", "password_configured": bool(APP_PASSWORD)}


@app.post("/api/auth")
def authenticate(body: AuthRequest):
    if not APP_PASSWORD:
        raise HTTPException(status_code=503, detail="اضبط APP_PASSWORD في متغيرات السيرفر أولًا.")
    if not hmac.compare_digest(body.password, APP_PASSWORD):
        raise HTTPException(status_code=401, detail="كلمة المرور غير صحيحة.")
    return {"ok": True}


@app.post("/api/info")
async def video_info(body: URLRequest, request: Request):
    require_auth(request)
    cleanup_expired()
    url = validate_url(body.url)
    try:
        return {"ok": True, **await run_in_threadpool(extract_info, url)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Metadata extraction failed for platform=%s", platform_for(url))
        detail = str(exc).casefold()
        if "bot" in detail or "captcha" in detail or "po token" in detail or "verification" in detail or "not a bot" in detail:
            message = "يوتيوب رفض الطلب من السيرفر باعتباره آليًا. تسجيل الدخول إلى YouTube على الآيفون أو الكمبيوتر لا ينقل جلسة حسابك إلى السيرفر. لا تدخل كلمة مرورك أو ترسل ملفات Cookies. افتح الفيديو الأصلي وجرّب لاحقًا؛ وإذا كان الفيديو ملكك، استخدم YouTube Studio لتنزيل نسختك."
        elif "javascript" in detail or "js runtime" in detail or "deno" in detail:
            message = "يوتيوب يحتاج إلى مكوّن JavaScript محدث على السيرفر. أعد المحاولة لاحقًا؛ وإذا استمر الخطأ، راجع Render Logs."
        elif "sign in" in detail or "login" in detail or "cookies" in detail or "private" in detail:
            message = "الفيديو يتطلب تسجيل دخول أو غير متاح للعامة. الأداة لا تستخدم جلسة YouTube الموجودة على جهازك، ولا تدخل كلمة مرورك هنا. جرّب رابطًا عامًا أو استخدم طريقة التنزيل الرسمية المتاحة."
        else:
            message = "مش قادر أقرأ الفيديو حاليًا. حدّث الأداة وجرّب رابطًا عامًا مباشرًا؛ تفاصيل الخطأ موجودة في Render Logs."
        raise HTTPException(status_code=422, detail=message)


@app.post("/api/download")
def create_download(body: DownloadRequest, request: Request):
    require_auth(request)
    cleanup_expired()
    url = validate_url(body.url)
    ip = client_ip(request)
    with lock:
        active = [job for job in jobs.values() if job.get("state") in ("queued", "downloading")]
        if sum(1 for job in active if job.get("client_ip") == ip) >= MAX_ACTIVE_JOBS_PER_IP:
            raise HTTPException(status_code=429, detail="عندك تنزيلات شغالة بالفعل. استنى لحد ما واحد يخلص.")
        if len(active) >= MAX_ACTIVE_JOBS_GLOBAL:
            raise HTTPException(status_code=429, detail="السيرفر مشغول حاليًا. جرّب بعد شوية.")
        job_id = uuid4().hex
        jobs[job_id] = {
            "state": "queued", "progress": 0, "message": "تم استلام الطلب...", "title": "",
            "created_at": time.time(), "client_ip": ip, "download_token": secrets.token_urlsafe(32),
        }
        executor.submit(perform_download, job_id, url, body.quality)
    return {"ok": True, "id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    require_auth(request)
    cleanup_expired()
    with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="طلب التنزيل غير موجود أو انتهت صلاحيته.")
        return public_job(job_id, job)


@app.get("/api/jobs/{job_id}/file-ticket")
def issue_download_ticket(job_id: str, request: Request, response: Response):
    require_auth(request)
    cleanup_expired()
    with lock:
        job = jobs.get(job_id)
        if not job or job.get("state") != "completed":
            raise HTTPException(status_code=404, detail="الملف غير جاهز أو انتهت صلاحيته.")
        response.set_cookie(
            key=f"nazzelha_dl_{job_id}", value=job["download_token"], max_age=60,
            path=f"/api/jobs/{job_id}/file", httponly=True,
            secure=request.url.scheme == "https", samesite="strict",
        )
        return {"file_url": f"/api/jobs/{job_id}/file"}


@app.get("/api/jobs/{job_id}/file")
def download_file(job_id: str, request: Request):
    cleanup_expired()
    with lock:
        job = jobs.get(job_id)
        if not job or job.get("state") != "completed":
            raise HTTPException(status_code=404, detail="الملف غير جاهز أو انتهت صلاحيته.")
        token = request.cookies.get(f"nazzelha_dl_{job_id}", "")
        if not hmac.compare_digest(token, job.get("download_token", "")):
            raise HTTPException(status_code=403, detail="رابط التنزيل غير صالح. ارجع للأداة واضغط تحميل الملف مرة أخرى.")
        folder = TEMP_ROOT / job_id
        path = folder / job.get("filename", "")
        if not path.is_file() or path.parent != folder:
            raise HTTPException(status_code=404, detail="ملف التنزيل لم يعد موجودًا.")
        import mimetypes
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, filename=path.name, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.exception_handler(HTTPException)
async def arabic_http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "حصل خطأ في الطلب."
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})
