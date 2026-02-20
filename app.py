import json
import math
import random
import threading
import time
from collections import deque
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

price_history: Dict[str, deque] = {
    c["ticker"]: deque([float(c["start_price"])] * 30, maxlen=30) for c in COMPANIES
}

players: Dict[str, Dict] = {}  # player -> {"cash": float, "holdings": {ticker: {"qty": int, "avg": float}}}

round_no = 0
status = "IDLE"  # IDLE or REACTION
reaction_start_ts: Optional[float] = None
reaction_end_ts: Optional[float] = None
current_news_internal: Optional[Dict] = None

# per-ticker drift (pct per tick)
drift_pct: Dict[str, float] = {t: 0.0 for t in prices.keys()}
vol_state: Dict[str, float] = {t: config.MARKET_NOISE_PCT for t in prices.keys()}
momentum_pct: Dict[str, float] = {t: 0.0 for t in prices.keys()}
fundamental_price: Dict[str, float] = {c["ticker"]: float(c["start_price"]) for c in COMPANIES}
sector_shock_pct: Dict[str, float] = {s: 0.0 for s in SECTORS}
market_shock_pct: float = 0.0

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
            "trades": [],
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
        "recent_trades": p.get("trades", [])[-8:],
    }

def reaction_meta() -> Dict:
    if status != "REACTION" or reaction_end_ts is None or reaction_start_ts is None:
        return {"active": False, "pulse": "CALM", "affected": 0, "progress": 0}

    affected = sum(1 for v in drift_pct.values() if abs(v) > 1e-8)
    mean_abs_drift = sum(abs(v) for v in drift_pct.values()) / max(1, len(drift_pct))
    pulse = "CALM"
    if mean_abs_drift >= 0.0010:
        pulse = "HIGH"
    elif mean_abs_drift >= 0.00045:
        pulse = "MEDIUM"

    elapsed = max(0.0, time.time() - reaction_start_ts)
    progress = min(100, int((elapsed / max(config.REACTION_SECONDS, 1)) * 100))
    return {"active": True, "pulse": pulse, "affected": affected, "progress": progress}

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

def quotes_for_all() -> Dict[str, Dict]:
    out = {}
    for t, px in prices.items():
        v = max(0.0005, vol_state.get(t, config.MARKET_NOISE_PCT))
        # Wider spread in volatile names.
        spread_pct = min(0.012, max(0.0008, v * 2.2))
        half = px * spread_pct * 0.5
        out[t] = {
            "bid": max(0.01, px - half),
            "ask": px + half,
            "spread_pct": spread_pct,
        }
    return out

def apply_news_effect(news: Dict):
    global status, reaction_start_ts, reaction_end_ts, current_news_internal, round_no

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
    reaction_start_ts = time.time()
    reaction_end_ts = time.time() + config.REACTION_SECONDS

def end_reaction_if_needed():
    global status, reaction_start_ts, reaction_end_ts, current_news_internal
    if status == "REACTION" and reaction_end_ts is not None and time.time() >= reaction_end_ts:
        for t in drift_pct.keys():
            drift_pct[t] = 0.0
        status = "IDLE"
        reaction_start_ts = None
        reaction_end_ts = None
        current_news_internal = None

def _step_shock(x: float, decay=0.88, shock_scale=0.00045) -> float:
    return (x * decay) + rng.gauss(0.0, shock_scale)

def market_tick():
    global prev_prices, market_shock_pct
    with state_lock:
        end_reaction_if_needed()
        prev_prices = dict(prices)

        market_shock_pct = _step_shock(market_shock_pct, decay=0.93, shock_scale=0.00038)
        for s in list(sector_shock_pct.keys()):
            sector_shock_pct[s] = _step_shock(sector_shock_pct[s], decay=0.90, shock_scale=0.00055)

        for t in list(prices.keys()):
            px = prices[t]
            c = TICKER_TO_COMPANY[t]
            sec = c["sector"]

            # Volatility clustering (GARCH-like simplified update).
            prev_ret = 0.0 if prev_prices[t] == 0 else (px - prev_prices[t]) / prev_prices[t]
            v_prev = vol_state.get(t, config.MARKET_NOISE_PCT)
            v_new = (0.86 * v_prev) + (0.12 * abs(prev_ret)) + 0.00012
            v_new = min(0.02, max(0.0006, v_new))
            vol_state[t] = v_new

            d = drift_pct.get(t, 0.0)
            if d != 0:
                # News impact decays over time instead of flat drift.
                drift_pct[t] *= 0.96

            # Fundamentals drift slowly; price mean-reverts gently to them.
            fundamental_price[t] *= (1.0 + rng.gauss(0.0, 0.00018))
            valuation_gap = (fundamental_price[t] - px) / max(px, 1.0)
            mean_revert = valuation_gap * 0.06

            # Momentum persistence, then partial decay.
            mom = momentum_pct.get(t, 0.0)
            momentum_term = mom * 0.32
            momentum_pct[t] = (mom * 0.65) + (prev_ret * 0.35)

            idio_noise = rng.gauss(0.0, v_new)
            pct = (
                idio_noise
                + d
                + mean_revert
                + momentum_term
                + market_shock_pct * 0.55
                + sector_shock_pct.get(sec, 0.0) * 0.65
            )

            new_px = max(1.0, px * (1.0 + pct))
            prices[t] = new_px
            price_history[t].append(new_px)

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
            "reaction_meta": reaction_meta(),
            "quotes": quotes_for_all(),
            "history": {t: list(h) for t, h in price_history.items()},
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
        quote = quotes_for_all().get(ticker) or {}
        mark_px = float(prices[ticker])
        bid_px = float(quote.get("bid", mark_px * 0.999))
        ask_px = float(quote.get("ask", mark_px * 1.001))
        spread_pct = float(quote.get("spread_pct", 0.001))

        # Size-based slippage: grows non-linearly with order size.
        slippage_pct = min(0.02, spread_pct * (0.35 + (math.sqrt(qty) * 0.11)))
        if side == "BUY":
            px = ask_px * (1.0 + slippage_pct)
        else:
            px = bid_px * (1.0 - slippage_pct)

        fee = max(1.0, px * qty * 0.0008)
        p = players[player]

        if side == "BUY":
            cost = (px * qty) + fee
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
            p["cash"] += (px * qty) - fee
            h["qty"] -= qty
            if h["qty"] == 0:
                del p["holdings"][ticker]

        p.setdefault("trades", []).append({
            "ts": int(time.time()),
            "ticker": ticker,
            "side": side,
            "qty": qty,
            "price": px,
            "fee": fee,
            "mark": mark_px,
        })
        if len(p["trades"]) > 100:
            p["trades"] = p["trades"][-100:]

        return jsonify({"ok": True, "fill_price": px, "fee": fee, "mark_price": mark_px})

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

    global prices, prev_prices, players, round_no, status, reaction_start_ts, reaction_end_ts, current_news_internal
    with state_lock:
        prices = {c["ticker"]: float(c["start_price"]) for c in COMPANIES}
        prev_prices = dict(prices)
        players = {}
        round_no = 0
        status = "IDLE"
        reaction_start_ts = None
        reaction_end_ts = None
        current_news_internal = None
        for t in drift_pct.keys():
            drift_pct[t] = 0.0
    return jsonify({"ok": True})

if __name__ == "__main__":
    # Local run only (Render should use gunicorn)
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
