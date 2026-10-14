FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip git && rm -rf /var/lib/apt/lists/*
# yt-dlp uses Deno as a JavaScript runtime for some YouTube extraction paths.
RUN curl -fsSL https://deno.land/install.sh | sh && ln -sf /root/.deno/bin/deno /usr/local/bin/deno
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY main.py ./main.py
COPY static ./static
COPY start.sh ./start.sh
RUN useradd --create-home --uid 10001 appuser && mkdir -p /tmp/nazzelha-downloads && chmod 755 /app/start.sh && chown -R appuser:appuser /app /tmp/nazzelha-downloads
# Install the matching PO-token provider source; its HTTP server will stay private to this container.
RUN git clone --single-branch --depth 1 --branch 2.0.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /home/appuser/bgutil-ytdlp-pot-provider && chown -R appuser:appuser /home/appuser/bgutil-ytdlp-pot-provider
USER appuser
ENV DENO_DIR=/home/appuser/.cache/deno
RUN mkdir -p /home/appuser/.cache/deno && cd /home/appuser/bgutil-ytdlp-pot-provider/server && deno install --allow-scripts=npm:canvas --frozen
WORKDIR /app
EXPOSE 8000
CMD ["/app/start.sh"]
