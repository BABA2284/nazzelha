FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip && rm -rf /var/lib/apt/lists/*
# yt-dlp uses Deno as a JavaScript runtime for some YouTube extraction paths.
RUN curl -fsSL https://deno.land/install.sh | sh && ln -sf /root/.deno/bin/deno /usr/local/bin/deno
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY main.py ./main.py
COPY static ./static
RUN useradd --create-home --uid 10001 appuser && mkdir -p /tmp/nazzelha-downloads && chown -R appuser:appuser /app /tmp/nazzelha-downloads
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
