#!/bin/sh
set -eu

PROVIDER_SERVER="/home/appuser/bgutil-ytdlp-pot-provider/server"

# The provider is bound to loopback only; it is not exposed publicly.
(
  cd "$PROVIDER_SERVER/node_modules"
  deno run --allow-env --allow-net --allow-ffi=. --allow-read=. ../src/main.ts --host 127.0.0.1 --port 4416
) &
PROVIDER_PID=$!

# Wait for the local PO-token provider to be ready before starting the web app.
i=0
while [ "$i" -lt 30 ]; do
  if python -c 'import socket; s=socket.create_connection(("127.0.0.1", 4416), timeout=0.5); s.close()' >/dev/null 2>&1; then
    exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
  fi
  if ! kill -0 "$PROVIDER_PID" 2>/dev/null; then
    echo "YouTube PO-token provider exited during startup; see logs above." >&2
    wait "$PROVIDER_PID" || true
    exit 1
  fi
  i=$((i + 1))
  sleep 1
done

echo "YouTube PO-token provider did not become ready within 30 seconds." >&2
kill "$PROVIDER_PID" 2>/dev/null || true
wait "$PROVIDER_PID" 2>/dev/null || true
exit 1
