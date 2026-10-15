FROM brainicism/bgutil-ytdlp-pot-provider:2.0.2-deno AS pot-provider

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip libstdc++6 && rm -rf /var/lib/apt/lists/*

# Reuse the provider's prebuilt Deno runtime instead of compiling its JS dependencies during this build.
COPY --from=pot-provider /usr/bin/deno /usr/local/bin/deno
COPY --from=pot-provider /app /opt/bgutil-pot-provider

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt && pip install --pre --upgrade "yt-dlp[default]"
COPY main.py ./main.py
COPY static ./static
COPY start.sh ./start.sh
RUN useradd --create-home --uid 10001 appuser && mkdir -p /tmp/nazzelha-downloads && chmod 755 /app/start.sh && chown -R appuser:appuser /app /opt/bgutil-pot-provider /tmp/nazzelha-downloads
ENV DENO_DIR=/opt/bgutil-pot-provider/.cache/deno
USER appuser
EXPOSE 8000
CMD ["/app/start.sh"]
