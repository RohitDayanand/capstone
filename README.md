# Fight Night

Kahoot-style live betting over a public-domain sports video. The host screen plays the video. Players join on their phones with a game PIN, link a mock bank account, and get a bettor number. Before known moments in the video, a bet opens. When the moment hits, the correct side splits the pot, and whoever called it **earliest** gets the biggest share.

The default game is a 1931 newsreel of Bep van Klaveren vs François Sybille for the European lightweight title in Rotterdam. The footage is from Polygoon Hollands Nieuws, public domain, via [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Bokswedstrijd.webm). It runs 4:42 and has four bets in it.

## Run it

```bash
uv sync
./demo.sh          # local server + public ngrok tunnel, prints the host URL
```

Open the printed host URL (`http://localhost:8000/?key=…`) on the big screen. When ngrok is running, the join link and QR code on the host screen point to the public ngrok URL, so anyone can scan in.

Without ngrok: `uv run python server.py` and players join on the same Wi-Fi at `http://<your-LAN-IP>:8000/play`.

## How the money works

- Linking a mock bank account (any 9-digit routing number and 6–17 digit account number) credits **$1,000** of play money.
- Each bet is one pick and one stake. You can't change it after you place it.
- All stakes go into the pot. At the reveal, winners split the **whole pot**, weighted by `stake × (1 + 2 × earliness)`:
  - `earliness` is 1 the moment betting opens and 0 at lock.
  - So the earliest correct bet counts 3×, and a last-second correct bet counts 1×.
- If nobody is right, everyone gets their stake back.

Example: Ana bets $100 early, Ben bets $100 late, and both are right. Cat bets $200 on the wrong side. The pot is $400. Ana gets $284.58 and Ben gets $115.42.

## The game clock

The host's video is the only clock. The host page reports its playhead about twice a second, and the server opens, locks and settles markets from that. Pausing pauses betting countdowns. Buffering doesn't count as playing. Markets only move forward, so scrubbing backwards can't reopen a locked bet. **Restart round** resets markets and balances, and keeps the players.

## Use a different video

Edit `game.json`: set `video_url` (any direct `.mp4`/`.webm` URL), the title, and the markets. Each market has `open_at`, `lock_at` and `reveal_at` in seconds, plus `options` and the index of the correct `answer`. Delete `media/fight.webm`, or set `GAME_FILE=other.json`. A local `media/fight.webm` always overrides `video_url`.

## Hosting

- **ngrok (recommended for a demo):** `./demo.sh`. Free, instant, and it uses your laptop.
- **Render free tier:** push to GitHub, then in Render choose *New → Blueprint* and pick this repo. `render.yaml` is ready. Get the host key from the service's Environment tab and open `https://<app>.onrender.com/?key=<HOST_KEY>`. Free instances sleep after 15 minutes idle (about a minute to wake), and state is in memory, so a restart wipes the game.
- **Vercel won't work as-is:** its serverless functions can't hold WebSocket connections or in-memory game state. You'd need to move the realtime layer to Pusher/Ably plus a KV store.

## Env vars

| Var | Default | |
| --- | --- | --- |
| `PORT` | 8000 | |
| `HOST_KEY` | random | Protects the host screen and controls |
| `PUBLIC_URL` | auto-detected ngrok URL | Base URL for the join link and QR code |
| `GAME_FILE` | `game.json` | |
