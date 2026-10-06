"""Kahoot-style live betting over a public-domain sports video.

The host page plays the video and reports its playhead to the server. Markets
open, lock and settle based on that playhead, so the host's video is the
single clock for everyone. Players join from their phones with the game PIN.
"""

import asyncio
import json
import os
import random
import secrets
import time
import urllib.request
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
LOCAL_VIDEO = ROOT / "media" / "fight.webm"  # optional local copy; avoids buffering from Wikimedia
GAME = json.loads((ROOT / os.environ.get("GAME_FILE", "game.json")).read_text())
HOST_KEY = os.environ.get("HOST_KEY") or secrets.token_urlsafe(6)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

# The earliest bettor's stake counts (1 + EARLY_BONUS)x; a last-second bet counts 1x.
EARLY_BONUS = 2.0
MIN_STAKE = 1
BANKS = ["Mock Chase", "Mock Bank of America", "Mock Wells Fargo", "Mock Citi", "Mock Capital One"]

STATUS_ORDER = ["upcoming", "open", "locked", "settled"]


@dataclass
class Player:
    id: str
    token: str
    name: str
    bettor_no: int
    bank: str | None = None
    account_last4: str | None = None
    balance: float = 0.0
    sockets: set = field(default_factory=set)

    def public(self):
        return {
            "id": self.id,
            "name": self.name,
            "bettor_no": self.bettor_no,
            "bank": self.bank,
            "account_last4": self.account_last4,
            "linked": self.bank is not None,
            "balance": round(self.balance, 2),
            "online": bool(self.sockets),
        }


@dataclass
class Bet:
    player_id: str
    option: int
    stake: float
    t: float  # video time the bet landed
    earliness: float  # 0 = right at lock, 1 = the instant the market opened
    payout: float = 0.0


class Game:
    def __init__(self):
        self.pin = f"{random.randint(0, 999999):06d}"
        self.players: dict[str, Player] = {}
        self.by_token: dict[str, Player] = {}
        self.hosts: set[WebSocket] = set()
        self.next_bettor = 101
        self.reset_round()

    def reset_round(self):
        self.t_ref = 0.0
        self.wall_ref = time.monotonic()
        self.playing = False
        self.status = {m["id"]: "upcoming" for m in GAME["markets"]}
        self.bets: dict[str, list[Bet]] = {m["id"]: [] for m in GAME["markets"]}
        for p in self.players.values():
            if p.bank:
                p.balance = GAME["starting_balance"]

    # --- clock -----------------------------------------------------------
    def now(self) -> float:
        if self.playing:
            return self.t_ref + (time.monotonic() - self.wall_ref)
        return self.t_ref

    def set_clock(self, t: float, playing: bool):
        self.t_ref = float(t)
        self.wall_ref = time.monotonic()
        self.playing = bool(playing)

    # --- markets ---------------------------------------------------------
    def advance(self) -> list[str]:
        """Move markets forward based on the playhead. Status never goes backwards,
        so scrubbing back can't reopen a locked market."""
        t = self.now()
        changed = []
        for m in GAME["markets"]:
            cur = self.status[m["id"]]
            target = "upcoming"
            if t >= m["reveal_at"]:
                target = "settled"
            elif t >= m["lock_at"]:
                target = "locked"
            elif t >= m["open_at"]:
                target = "open"
            if STATUS_ORDER.index(target) > STATUS_ORDER.index(cur):
                if target == "settled":
                    self.settle(m)
                self.status[m["id"]] = target
                changed.append(m["id"])
        return changed

    def settle(self, m):
        bets = self.bets[m["id"]]
        pot = sum(b.stake for b in bets)
        winners = [b for b in bets if b.option == m["answer"]]
        if not winners:
            # Nobody called it: everyone gets their stake back.
            for b in bets:
                b.payout = b.stake
        else:
            weights = {id(b): b.stake * (1 + EARLY_BONUS * b.earliness) for b in winners}
            total_w = sum(weights.values())
            for b in winners:
                b.payout = round(pot * weights[id(b)] / total_w, 2)
        for b in bets:
            self.players[b.player_id].balance += b.payout

    def place_bet(self, p: Player, market_id: str, option: int, stake: float) -> str | None:
        m = next((m for m in GAME["markets"] if m["id"] == market_id), None)
        if m is None:
            return "Unknown market."
        if self.status[market_id] != "open":
            return "Betting is closed for this one."
        if not p.bank:
            return "Link a bank account first."
        if any(b.player_id == p.id for b in self.bets[market_id]):
            return "You already bet on this one."
        if option not in range(len(m["options"])):
            return "Pick an option."
        stake = round(float(stake), 2)
        if stake < MIN_STAKE:
            return f"Minimum stake is ${MIN_STAKE}."
        if stake > p.balance + 1e-9:
            return "Insufficient funds."
        t = self.now()
        span = m["lock_at"] - m["open_at"]
        earliness = max(0.0, min(1.0, (m["lock_at"] - t) / span))
        p.balance -= stake
        self.bets[market_id].append(Bet(p.id, option, stake, t, earliness))
        return None

    # --- views -----------------------------------------------------------
    def market_view(self, m, for_player: Player | None = None, host=False):
        st = self.status[m["id"]]
        bets = self.bets[m["id"]]
        v = {
            "id": m["id"],
            "question": m["question"],
            "options": m["options"],
            "open_at": m["open_at"],
            "lock_at": m["lock_at"],
            "reveal_at": m["reveal_at"],
            "status": st,
            "pot": round(sum(b.stake for b in bets), 2),
            "n_bets": len(bets),
        }
        if host or st == "settled":
            v["counts"] = [sum(1 for b in bets if b.option == i) for i in range(len(m["options"]))]
        if st == "settled":
            v["answer"] = m["answer"]
            v["reveal_text"] = m.get("reveal_text", "")
            ranked = sorted(bets, key=lambda b: b.payout - b.stake, reverse=True)
            v["results"] = [
                {
                    "name": self.players[b.player_id].name,
                    "bettor_no": self.players[b.player_id].bettor_no,
                    "option": b.option,
                    "stake": b.stake,
                    "payout": b.payout,
                    "earliness": round(b.earliness, 3),
                }
                for b in ranked
            ]
        if for_player:
            mine = next((b for b in bets if b.player_id == for_player.id), None)
            if mine:
                v["my_bet"] = {
                    "option": mine.option,
                    "stake": mine.stake,
                    "earliness": round(mine.earliness, 3),
                    "payout": mine.payout if st == "settled" else None,
                }
        return v

    def leaderboard(self):
        ps = [p for p in self.players.values() if p.bank]
        ps.sort(key=lambda p: p.balance, reverse=True)
        return [p.public() for p in ps]

    def host_state(self):
        return {
            "type": "state",
            "pin": self.pin,
            "t": self.now(),
            "playing": self.playing,
            "game": {
                **{k: GAME[k] for k in ("title", "video_credit", "starting_balance")},
                "video_url": "/media/fight.webm" if LOCAL_VIDEO.exists() else GAME["video_url"],
            },
            "early_bonus": EARLY_BONUS,
            "markets": [self.market_view(m, host=True) for m in GAME["markets"]],
            "players": [p.public() for p in self.players.values()],
            "leaderboard": self.leaderboard(),
            "join_url": join_url(self.pin),
        }

    def player_state(self, p: Player):
        return {
            "type": "state",
            "t": self.now(),
            "playing": self.playing,
            "title": GAME["title"],
            "early_bonus": EARLY_BONUS,
            "min_stake": MIN_STAKE,
            "banks": BANKS,
            "me": p.public(),
            "markets": [self.market_view(m, for_player=p) for m in GAME["markets"]],
            "leaderboard": self.leaderboard()[:10],
        }

    # --- fan-out ---------------------------------------------------------
    async def broadcast(self):
        await asyncio.gather(
            *(safe_send(ws, self.host_state()) for ws in list(self.hosts)),
            *(safe_send(ws, self.player_state(p)) for p in self.players.values() for ws in list(p.sockets)),
        )

    async def tick(self):
        """Cheap clock sync so countdowns stay honest between full state pushes."""
        msg = {"type": "tick", "t": self.now(), "playing": self.playing}
        await asyncio.gather(*(safe_send(ws, msg) for p in self.players.values() for ws in list(p.sockets)))


async def safe_send(ws: WebSocket, msg):
    try:
        await ws.send_json(msg)
    except Exception:
        pass


_ngrok_cache: dict = {"url": None, "at": 0.0}


def detect_ngrok() -> str | None:
    """If an ngrok agent is running locally, use its public URL for the join link."""
    if time.monotonic() - _ngrok_cache["at"] < 10:
        return _ngrok_cache["url"]
    url = None
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=0.3) as r:
            tunnels = json.load(r).get("tunnels", [])
            url = next((t["public_url"] for t in tunnels if t["public_url"].startswith("https")), None)
    except Exception:
        pass
    _ngrok_cache.update(url=url, at=time.monotonic())
    return url


def join_url(pin: str) -> str | None:
    base = PUBLIC_URL or detect_ngrok()
    return f"{base}/play?pin={pin}" if base else None


game = Game()


@asynccontextmanager
async def lifespan(app):
    async def loop():
        n = 0
        while True:
            await asyncio.sleep(0.25)
            n += 1
            if game.advance():
                await game.broadcast()
            elif n % 2 == 0:
                await game.tick()

    task = asyncio.create_task(loop())
    print(f"\n  Host screen:  http://localhost:{os.environ.get('PORT', '8000')}/?key={HOST_KEY}\n  Game PIN:     {game.pin}\n", flush=True)
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def no_cache_pages(request, call_next):
    # Pages and scripts are tiny; always revalidate so edits show up on refresh.
    resp = await call_next(request)
    if not request.url.path.startswith("/media"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/static", StaticFiles(directory=STATIC), name="static")
(ROOT / "media").mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory=ROOT / "media"), name="media")


@app.get("/")
async def index(key: str = ""):
    if key == HOST_KEY:
        return FileResponse(STATIC / "host.html")
    return FileResponse(STATIC / "landing.html")


@app.get("/play")
async def play():
    return FileResponse(STATIC / "play.html")


@app.get("/healthz")
async def healthz():
    return JSONResponse({"ok": True})


@app.websocket("/ws/host")
async def ws_host(ws: WebSocket, key: str = ""):
    await ws.accept()
    if key != HOST_KEY:
        await ws.send_json({"type": "error", "message": "Bad host key."})
        await ws.close()
        return
    game.hosts.add(ws)
    await ws.send_json(game.host_state())
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "clock":
                was_playing = game.playing
                game.set_clock(msg.get("t", 0), msg.get("playing", False))
                changed = game.advance()
                if changed or was_playing != game.playing or msg.get("seek"):
                    await game.broadcast()
            elif kind == "reset":
                game.reset_round()
                await game.broadcast()
            elif kind == "kick":
                p = game.players.pop(msg.get("player_id"), None)
                if p:
                    game.by_token.pop(p.token, None)
                    for s in list(p.sockets):
                        await safe_send(s, {"type": "kicked"})
                    await game.broadcast()
    except WebSocketDisconnect:
        pass
    finally:
        game.hosts.discard(ws)


@app.websocket("/ws/play")
async def ws_play(ws: WebSocket):
    await ws.accept()
    me: Player | None = None
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")

            if kind == "hello":
                p = game.by_token.get(msg.get("token") or "")
                if p:
                    me = p
                    me.sockets.add(ws)
                    await ws.send_json({"type": "welcome", "token": me.token})
                    await game.broadcast()
                else:
                    await ws.send_json({"type": "need_join"})

            elif kind == "join":
                if str(msg.get("pin", "")).strip() != game.pin:
                    await ws.send_json({"type": "error", "message": "Wrong game PIN."})
                    continue
                name = str(msg.get("name", "")).strip()[:24]
                if not name:
                    await ws.send_json({"type": "error", "message": "Pick a nickname."})
                    continue
                if any(p.name.lower() == name.lower() for p in game.players.values()):
                    await ws.send_json({"type": "error", "message": "That nickname is taken."})
                    continue
                me = Player(secrets.token_hex(4), secrets.token_urlsafe(16), name, game.next_bettor)
                game.next_bettor += 1
                me.sockets.add(ws)
                game.players[me.id] = me
                game.by_token[me.token] = me
                await ws.send_json({"type": "welcome", "token": me.token})
                await game.broadcast()

            elif me is None:
                await ws.send_json({"type": "need_join"})

            elif kind == "link_bank":
                bank = msg.get("bank")
                acct = "".join(c for c in str(msg.get("account", "")) if c.isdigit())
                routing = "".join(c for c in str(msg.get("routing", "")) if c.isdigit())
                if bank not in BANKS:
                    err = "Pick a bank."
                elif not 6 <= len(acct) <= 17:
                    err = "Account number should be 6–17 digits."
                elif len(routing) != 9:
                    err = "Routing number should be 9 digits."
                else:
                    err = None
                if err:
                    await ws.send_json({"type": "error", "message": err})
                    continue
                if not me.bank:
                    me.balance = GAME["starting_balance"]
                me.bank, me.account_last4 = bank, acct[-4:]
                await game.broadcast()

            elif kind == "bet":
                try:
                    err = game.place_bet(me, msg.get("market"), int(msg.get("option", -1)), msg.get("stake", 0))
                except (TypeError, ValueError):
                    err = "Invalid bet."
                if err:
                    await ws.send_json({"type": "error", "message": err})
                else:
                    await game.broadcast()
    except WebSocketDisconnect:
        pass
    finally:
        if me:
            me.sockets.discard(ws)
            await game.broadcast()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
