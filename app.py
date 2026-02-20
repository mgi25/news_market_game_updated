import json
import random
import threading
import time
from typing import Dict, Optional, List

from flask import Flask, render_template, request, jsonify, redirect

import config

app = Flask(__name__)

# -------------------- Data loading --------------------
with open("data/companies.json", "r", encoding="utf-8") as f:
    COMPANIES = json.load(f)

with open("data/news.json", "r", encoding="utf-8") as f:
    NEWS = json.load(f)

TICKER_TO_COMPANY = {c["ticker"]: c for c in COMPANIES}
SECTORS = sorted({c["sector"] for c in COMPANIES})

# -------------------- Game state --------------------
state_lock = threading.Lock()

prices: Dict[str, float] = {c["ticker"]: float(c["start_price"]) for c in COMPANIES}
prev_prices: Dict[str, float] = dict(prices)

players: Dict[str, Dict] = {}  # player -> {"cash": float, "holdings": {ticker: {"qty": int, "avg": float}}}

round_no = 0
status = "IDLE"  # IDLE or REACTION
reaction_end_ts: Optional[float] = None
current_news_internal: Optional[Dict] = None

# per-ticker drift (pct per tick)
drift_pct: Dict[str, float] = {t: 0.0 for t in prices.keys()}

rng = random.Random(42)

# -------------------- Background thread (Render/Gunicorn safe) --------------------
tick_thread_started = False
tick_thread_lock = threading.Lock()


def background_loop():
    while True:
        time.sleep(config.TICK_SECONDS)
        market_tick()


def ensure_tick_thread():
    """
    Start the tick thread lazily (first request) to avoid Gunicorn fork/preload issues.
    """
    global tick_thread_started
    if tick_thread_started:
        return
    with tick_thread_lock:
        if tick_thread_started:
            return
        threading.Thread(target=background_loop, daemon=True).start()
        tick_thread_started = True


@app.before_request
def _start_bg_once():
    ensure_tick_thread()

# -------------------- Helpers --------------------
def ensure_player(name: str):
    if name not in players:
        players[name] = {
            "cash": float(config.START_CASH),
            "holdings": {},
        }

def portfolio_value(name: str) -> Dict:
    ensure_player(name)
    p = players[name]
    cash = p["cash"]
    holdings_value = 0.0
    for t, h in p["holdings"].items():
        holdings_value += prices.get(t, 0.0) * h["qty"]
    total = cash + holdings_value
    return {
        "cash": cash,
        "holdings_value": holdings_value,
        "total_value": total,
        "holdings": p["holdings"],
    }

def compute_leaderboard() -> List[Dict]:
    out = []
    for name in list(players.keys()):
        v = portfolio_value(name)
        out.append({"player": name, "total": v["total_value"]})
    out.sort(key=lambda x: x["total"], reverse=True)
    return out

def public_news(n: Optional[Dict]) -> Optional[Dict]:
    if not n:
        return None
    # Do not leak direction/intensity/sectors/tickers to players/presenter.
    return {
        "id": n.get("id"),
        "headline": n.get("headline"),
        "summary": n.get("summary"),
        "body": n.get("body"),
        "bullets": n.get("bullets") or [],
    }

def seconds_left() -> Optional[int]:
    if status != "REACTION" or reaction_end_ts is None:
        return None
    return max(0, int(reaction_end_ts - time.time()))

def movers_top(n=6) -> List[Dict]:
    moves = []
    for t, px in prices.items():
        last = prev_prices.get(t, px)
        pct = 0.0 if last == 0 else (px - last) / last
        c = TICKER_TO_COMPANY[t]
        moves.append({
            "ticker": t,
            "name": c["name"],
            "sector": c["sector"],
            "price": px,
            "pct": pct,
        })
    moves.sort(key=lambda x: abs(x["pct"]), reverse=True)
    return moves[:n]

def apply_news_effect(news: Dict):
    global status, reaction_end_ts, current_news_internal, round_no

    direction = news["direction"]      # hidden
    intensity = news["intensity"]      # hidden
    sectors = news.get("sectors", [])
    tickers = news.get("tickers", [])

    lo, hi = config.INTENSITY_RANGES[intensity]
    total_pct = rng.uniform(lo, hi)
    if direction == "DOWN":
        total_pct = -total_pct

    ticks = max(1, int(config.REACTION_SECONDS / max(config.TICK_SECONDS, 0.25)))
    base_drift = total_pct / ticks

    sector_set = set(sectors)
    direct = set(tickers)

    # If sector news has no direct tickers, affect all companies in that sector.
    if not direct and sectors:
        for c in COMPANIES:
            if c["sector"] in sector_set:
                direct.add(c["ticker"])

    linked_sectors = set()
    for s in sector_set:
        for ls in config.SECTOR_LINKS.get(s, []):
            linked_sectors.add(ls)

    # reset all drift first
    for t in drift_pct.keys():
        drift_pct[t] = 0.0

    for t in drift_pct.keys():
        c = TICKER_TO_COMPANY[t]
        if t in direct:
            w = config.DIRECT_WEIGHT
        elif c["sector"] in sector_set:
            w = config.SECTOR_WEIGHT
        elif c["sector"] in linked_sectors:
            w = config.LINKED_WEIGHT
        else:
            w = 0.0
        drift_pct[t] = base_drift * w

    round_no += 1
    current_news_internal = news
    status = "REACTION"
    reaction_end_ts = time.time() + config.REACTION_SECONDS

def end_reaction_if_needed():
    global status, reaction_end_ts, current_news_internal
    if status == "REACTION" and reaction_end_ts is not None and time.time() >= reaction_end_ts:
        for t in drift_pct.keys():
            drift_pct[t] = 0.0
        status = "IDLE"
        reaction_end_ts = None
        current_news_internal = None

def market_tick():
    global prev_prices
    with state_lock:
        end_reaction_if_needed()
        prev_prices = dict(prices)

        for t in list(prices.keys()):
            px = prices[t]
            noise = rng.uniform(-config.MARKET_NOISE_PCT, config.MARKET_NOISE_PCT)
            d = drift_pct.get(t, 0.0)
            jitter = rng.uniform(-abs(d) * 0.35, abs(d) * 0.35) if d != 0 else 0.0
            pct = noise + d + jitter
            prices[t] = max(1.0, px * (1.0 + pct))

# -------------------- Routes --------------------
@app.get("/")
def index():
    return render_template("index.html", title="Join | News Market Game")

@app.get("/game")
def game():
    player = (request.args.get("player") or "").strip()
    if not player:
        return redirect("/")
    with state_lock:
        ensure_player(player)
    return render_template("game.html", title="Game | News Market Game", player_name=player)

@app.get("/presenter")
def presenter():
    return render_template("presenter.html", title="Presenter | News Market Game", player_name=None)

@app.get("/admin")
def admin():
    return render_template("admin.html", title="Admin | News Market Game", player_name=None)

# -------------------- APIs --------------------
@app.get("/api/bootstrap")
def api_bootstrap():
    return jsonify({"companies": COMPANIES, "sectors": SECTORS})

@app.get("/api latest_state")
def api_state():
    player = (request.args.get("player") or "").strip()
    with state_lock:
        port = portfolio_value(player) if player else None
        out = {
            "round": round_no,
            "status": status,
            "timer_s": seconds_left(),
            "news": public_news(current_news_internal),
            "prices": prices,
            "leaderboard": compute_leaderboard(),
            "movers": movers_top(6),
        }
        if port:
            out["portfolio"] = port
        return jsonify(out)

# NOTE: keep old route name too (if your frontend calls /api/state)
@app.get("/api/state")
def api_state_alias():
    return api_state()

@app.post("/api/trade")
def api_trade():
    data = request.get_json(force=True, silent=True) or {}
    player = (data.get("player") or "").strip()
    ticker = (data.get("ticker") or "").strip().upper()
    side = (data.get("side") or "").strip().upper()
    qty = int(data.get("qty") or 0)

    if not player or ticker not in prices or side not in ("BUY", "SELL") or qty <= 0:
        return jsonify({"ok": False, "error": "Invalid trade"}), 400

    with state_lock:
        ensure_player(player)
        px = float(prices[ticker])
        p = players[player]

        if side == "BUY":
            cost = px * qty
            if p["cash"] < cost:
                return jsonify({"ok": False, "error": "Not enough cash"}), 400
            p["cash"] -= cost
            h = p["holdings"].get(ticker)
            if not h:
                p["holdings"][ticker] = {"qty": qty, "avg": px}
            else:
                new_qty = h["qty"] + qty
                new_avg = (h["avg"] * h["qty"] + px * qty) / new_qty
                h["qty"] = new_qty
                h["avg"] = new_avg

        else:  # SELL
            h = p["holdings"].get(ticker)
            if not h or h["qty"] < qty:
                return jsonify({"ok": False, "error": "Not enough holdings"}), 400
            p["cash"] += px * qty
            h["qty"] -= qty
            if h["qty"] == 0:
                del p["holdings"][ticker]

        return jsonify({"ok": True})

# -------- Admin APIs --------
@app.post("/api/admin/login")
def api_admin_login():
    data = request.get_json(force=True, silent=True) or {}
    if (data.get("password") or "") == config.ADMIN_PASSWORD:
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401

@app.get("/api/admin/state")
def api_admin_state():
    with state_lock:
        return jsonify({
            "round": round_no,
            "status": status,
            "timer_s": seconds_left(),
            "headline": (current_news_internal or {}).get("headline"),
        })

@app.get("/api/admin/news")
def api_admin_news():
    return jsonify({"news": NEWS})

def check_admin(password: str) -> bool:
    return password == config.ADMIN_PASSWORD

@app.post("/api/admin/trigger")
def api_admin_trigger():
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password") or ""
    news_id = data.get("news_id") or ""
    if not check_admin(password):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    item = next((n for n in NEWS if n.get("id") == news_id), None)
    if not item:
        return jsonify({"ok": False, "error": "News not found"}), 404

    with state_lock:
        apply_news_effect(item)
    return jsonify({"ok": True})

@app.post("/api/admin/random")
def api_admin_random():
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password") or ""
    if not check_admin(password):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    item = random.choice(NEWS)
    with state_lock:
        apply_news_effect(item)
    return jsonify({"ok": True, "news_id": item.get("id")})

@app.post("/api/admin/reset")
def api_admin_reset():
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password") or ""
    if not check_admin(password):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    global prices, prev_prices, players, round_no, status, reaction_end_ts, current_news_internal
    with state_lock:
        prices = {c["ticker"]: float(c["start_price"]) for c in COMPANIES}
        prev_prices = dict(prices)
        players = {}
        round_no = 0
        status = "IDLE"
        reaction_end_ts = None
        current_news_internal = None
        for t in drift_pct.keys():
            drift_pct[t] = 0.0
    return jsonify({"ok": True})

if __name__ == "__main__":
    # Local run only (Render should use gunicorn)
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
