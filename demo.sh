#!/usr/bin/env bash
# Run the game locally and expose it publicly through ngrok.
# The join link and QR code on the host screen pick up the ngrok URL automatically.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
export HOST_KEY="${HOST_KEY:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(6))')}"

if [ ! -f media/fight.webm ]; then
  echo "Downloading the fight video (38 MB) for smooth local playback..."
  mkdir -p media
  curl -sL -o media/fight.webm "https://upload.wikimedia.org/wikipedia/commons/0/07/Bokswedstrijd.webm"
fi

ngrok http "$PORT" --log=stdout > /tmp/fightnight-ngrok.log 2>&1 &
NGROK_PID=$!
trap 'kill $NGROK_PID 2>/dev/null' EXIT

echo "Open the host screen at: http://localhost:$PORT/?key=$HOST_KEY"
PORT="$PORT" .venv/bin/python server.py
