from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets
import shutil
import threading
import time
import httpx
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth, OAuthError

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMP_ROOT = Path(os.getenv("DOWNLOAD_DIR", "/tmp/nazzelha-downloads"))
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://nazzelha.onrender.com").strip().rstrip("/")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
FACEBOOK_CLIENT_ID = os.getenv("FACEBOOK_CLIENT_ID", "").strip()
FACEBOOK_CLIENT_SECRET = os.getenv("FACEBOOK_CLIENT_SECRET", "").strip()
TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
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

app = FastAPI(title="Nazzelha - نزّلها", version="1.2.0")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET or secrets.token_urlsafe(48), same_site="lax", https_only=True, max_age=60 * 60 * 24 * 14)
oauth = OAuth()
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(name="google", client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET, server_metadata_url="https://accounts.google.com/.well-known/openid-configuration", client_kwargs={"scope": "openid email profile"})
if FACEBOOK_CLIENT_ID and FACEBOOK_CLIENT_SECRET:
    oauth.register(name="facebook", client_id=FACEBOOK_CLIENT_ID, client_secret=FACEBOOK_CLIENT_SECRET, authorize_url="https://www.facebook.com/v20.0/dialog/oauth", access_token_url="https://graph.facebook.com/v20.0/oauth/access_token", api_base_url="https://graph.facebook.com/v20.0/", client_kwargs={"scope": "email,public_profile"})
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
lock = threading.RLock()
jobs: dict[str, dict] = {}
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nazzelha-download")


class URLRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class DownloadRequest(URLRequest):
    quality: str = Field(default="best", pattern="^(best|1080|720|480|audio)$")


def client_ip(request: Request) -> str:
    # Behind a trusted hosting proxy, request.client.host is the proxy address.
    # Use the first forwarded value only when the app is deployed behind that proxy.
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded[:64] if forwarded else (request.client.host if request.client else "unknown")


AUTH_SESSION_VERSION = 2  # Increment to invalidate all previously issued login sessions.


def require_auth(request: Request) -> None:
    if public_user(request):
        return
    request.session.clear()
    raise HTTPException(status_code=401, detail="انتهت جلسة الدخول. سجّل الدخول مرة أخرى باستخدام Google أو Facebook أو TikTok.")


def provider_enabled(provider: str) -> bool:
    return {
        "google": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "facebook": bool(FACEBOOK_CLIENT_ID and FACEBOOK_CLIENT_SECRET),
        "tiktok": bool(TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET),
    }.get(provider, False)


def public_user(request: Request) -> dict | None:
    user = request.session.get("user")
    if request.session.get("auth_version") != AUTH_SESSION_VERSION or not isinstance(user, dict):
        return None
    return {key: user.get(key) for key in ("id", "name", "email", "avatar", "provider") if user.get(key)}


def oauth_redirect_uri(provider: str) -> str:
    return f"{PUBLIC_BASE_URL}/auth/{provider}/callback"


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
        "socket_timeout": 15, "retries": 2, "fragment_retries": 2,
        "cachedir": False, "ignoreerrors": False, "restrictfilenames": True,
        "trim_file_name": 120, "max_filesize": MAX_FILE_BYTES,
        "extractor_retries": 2,
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
            info = data.get("info_dict") or {}
            if info.get("title") and not job.get("title"):
                job["title"] = str(info["title"])[:240]
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
        opts = ydl_base_options()
        def reject_oversized_or_long(info: dict, *, incomplete: bool) -> str | None:
            duration = info.get("duration")
            if duration and duration > MAX_DURATION_SECONDS:
                return f"مدة الفيديو أطول من الحد المسموح ({MAX_DURATION_SECONDS // 3600} ساعات)."
            size = info.get("filesize") or info.get("filesize_approx")
            if size and size > MAX_FILE_BYTES:
                return f"حجم الملف أكبر من الحد المسموح ({MAX_FILE_BYTES // (1024 * 1024)} ميجابايت)."
            return None
        opts.update({
            "outtmpl": str(folder / "%(title).100s-%(id)s.%(ext)s"),
            "format": format_selector(quality), "merge_output_format": "mp4",
            "progress_hooks": [progress_hook(job_id)], "overwrites": True,
            "match_filter": reject_oversized_or_long,
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
    return {"ok": True, "app": "nazzelha", "authentication": "social_login_required"}


@app.get("/api/auth/providers")
def auth_providers():
    return {"providers": {"google": provider_enabled("google"), "facebook": provider_enabled("facebook"), "tiktok": provider_enabled("tiktok")}, "password": False, "registration_required": True}


@app.get("/api/me")
def current_user(request: Request):
    return {"authenticated": bool(public_user(request)), "user": public_user(request)}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/auth/{provider}")
async def start_social_login(provider: str, request: Request):
    if not provider_enabled(provider):
        raise HTTPException(status_code=503, detail=f"تسجيل الدخول بـ {provider} غير مفعّل بعد على السيرفر.")
    redirect_uri = oauth_redirect_uri(provider)
    if provider in ("google", "facebook"):
        return await oauth.create_client(provider).authorize_redirect(request, redirect_uri)
    state = secrets.token_urlsafe(32)
    request.session["tiktok_oauth_state"] = state
    from urllib.parse import urlencode
    params = {"client_key": TIKTOK_CLIENT_KEY, "response_type": "code", "scope": "user.info.basic", "redirect_uri": redirect_uri, "state": state}
    return RedirectResponse("https://www.tiktok.com/v2/auth/authorize/?" + urlencode(params))


@app.get("/auth/{provider}/callback")
async def social_login_callback(provider: str, request: Request):
    if not provider_enabled(provider):
        raise HTTPException(status_code=503, detail="طريقة تسجيل الدخول غير مفعّلة على السيرفر.")
    try:
        if provider in ("google", "facebook"):
            client = oauth.create_client(provider)
            token = await client.authorize_access_token(request)
            if provider == "google":
                profile = token.get("userinfo")
                if not profile:
                    response = await client.get("https://openidconnect.googleapis.com/v1/userinfo", token=token)
                    response.raise_for_status()
                    profile = response.json()
                user = {"id": str(profile.get("sub") or ""), "name": str(profile.get("name") or "مستخدم Google")[:120], "email": str(profile.get("email") or "")[:254], "avatar": str(profile.get("picture") or "")[:2048], "provider": "google"}
            else:
                response = await client.get("me", params={"fields": "id,name,email,picture.type(large)"}, token=token)
                response.raise_for_status()
                profile = response.json()
                picture = (profile.get("picture") or {}).get("data") or {}
                user = {"id": str(profile.get("id") or ""), "name": str(profile.get("name") or "مستخدم Facebook")[:120], "email": str(profile.get("email") or "")[:254], "avatar": str(picture.get("url") or "")[:2048], "provider": "facebook"}
        elif provider == "tiktok":
            expected_state = request.session.pop("tiktok_oauth_state", "")
            if not expected_state or not hmac.compare_digest(expected_state, request.query_params.get("state", "")) or not request.query_params.get("code"):
                raise HTTPException(status_code=400, detail="تعذّر التحقق من جلسة TikTok. جرّب تسجيل الدخول مرة أخرى.")
            async with httpx.AsyncClient(timeout=15) as client:
                token_response = await client.post("https://open.tiktokapis.com/v2/oauth/token/", data={"client_key": TIKTOK_CLIENT_KEY, "client_secret": TIKTOK_CLIENT_SECRET, "code": request.query_params["code"], "grant_type": "authorization_code", "redirect_uri": oauth_redirect_uri("tiktok")}, headers={"Content-Type": "application/x-www-form-urlencoded"})
                token_response.raise_for_status()
                access_token = token_response.json().get("access_token")
                if not access_token:
                    raise ValueError("TikTok did not return an access token")
                profile_response = await client.get("https://open.tiktokapis.com/v2/user/info/", params={"fields": "open_id,display_name,avatar_url"}, headers={"Authorization": f"Bearer {access_token}"})
                profile_response.raise_for_status()
                profile = (profile_response.json().get("data") or {}).get("user") or {}
            user = {"id": str(profile.get("open_id") or ""), "name": str(profile.get("display_name") or "مستخدم TikTok")[:120], "email": "", "avatar": str(profile.get("avatar_url") or "")[:2048], "provider": "tiktok"}
        else:
            raise HTTPException(status_code=404, detail="طريقة تسجيل الدخول غير معروفة.")
        if not user.get("id"):
            raise ValueError("The provider did not return a stable user ID")
        request.session.clear()
        request.session["user"] = user
        request.session["auth_version"] = AUTH_SESSION_VERSION
        return RedirectResponse("/", status_code=303)
    except HTTPException:
        raise
    except (OAuthError, httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("Social login failed for provider=%s: %s", provider, type(exc).__name__)
        return RedirectResponse("/?auth_error=1", status_code=303)


@app.post("/api/auth")
def authenticate():
    raise HTTPException(status_code=410, detail="تم إيقاف الدخول بكلمة مرور مشتركة. استخدم التسجيل بحسابك الاجتماعي.")


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
