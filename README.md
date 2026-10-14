# Nazzelha | نزّلها

A mobile-first Arabic RTL web app prototype for processing supported public video links and saving content the user owns or is authorized to download.

**Status: initial implementation committed; deployment and real-provider downloads still need verification.**

## Features

- Responsive Arabic RTL interface designed for iPhone and mobile browsers.
- PWA manifest and service worker for adding the web app to the iPhone Home Screen.
- Link metadata lookup and format selection for supported public URLs.
- Background download jobs with progress status.
- Password-protected API routes and short-lived download tickets.
- File-size, video-duration, and active-job limits.
- Docker-based runtime with FFmpeg.

## Tech stack

- **Frontend:** HTML, CSS, JavaScript, PWA
- **Backend:** Python, FastAPI, Pydantic
- **Media processing:** yt-dlp, FFmpeg
- **Packaging/deployment:** Docker, Docker Compose, Render Blueprint

## Supported providers

The initial backend accepts URL domains for YouTube, Instagram, Facebook, and TikTok. Acceptance of a domain does **not** guarantee that every link works: providers change their systems, and private or login-gated media is not supported. Real end-to-end provider tests have not yet been completed.

## Run locally

Requirements: Docker Engine or Docker Desktop.

```bash
cp .env.example .env
```

Edit `.env` and replace `APP_PASSWORD` with a strong, unique password. Then run:

```bash
docker compose up --build -d
```

Open `http://localhost:8000`. Health endpoint: `http://localhost:8000/api/health`.

## Deploy to Render

1. Open Render and choose **New → Blueprint**.
2. Connect this GitHub repository and let Render read `render.yaml`.
3. Set a strong, unique `APP_PASSWORD` as a secret environment variable.
4. Deploy, then verify the `/api/health` endpoint and HTTPS site URL.

The Blueprint currently requests a paid `starter` service. Review the current plan and pricing in Render before confirming deployment. A public repository does not need any passwords or API keys committed to it.

## Add to iPhone Home Screen

1. Open the deployed HTTPS URL in Safari.
2. Tap **Share**.
3. Choose **Add to Home Screen**, then **Add**.

## Responsible use

Only download media you own or are authorized to save, and follow platform terms and applicable law. This project does not aim to bypass DRM, paywalls, private-account controls, or other access restrictions. Use official download options when required.

## Security notes

- Never commit `.env`, passwords, tokens, or production secrets.
- Set `APP_PASSWORD` through the hosting provider's environment-variable settings.
- Temporary downloads are stored on the server before being transferred to the device.
- Review hosting storage, memory, CPU, request limits, and retention before public use.
- This initial implementation has not yet undergone a full production security audit.

## Project checklist

- [x] Initial API and responsive Arabic interface
- [x] Docker and Render configuration
- [x] Arabic and English project documentation
- [ ] Add automated tests and continuous integration
- [ ] Deploy and verify health check
- [ ] Test allowed sample URLs on each provider
- [ ] Add screenshots and a verified live demo

## License

No license has been selected. Unless a license is added, standard copyright applies and reuse is not granted by default.
