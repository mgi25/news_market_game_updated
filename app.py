import json
import math
import random
import threading
import time
from typing import Dict, Optional, List, Tuple

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

# -------------------- Shared state --------------------
state_lock = threading.Lock()

# Market state per ticker
prices: Dict[str, float] = {c["ticker"]: float(c["start_price"]) for c in COMPANIES}
prev_prices: Dict[str, float] = dict(prices)

vol: Dict[str, float] = {}
trend: Dict[str, float] = {}
shock_vol: Dict[str, float] = {}
fair_value: Dict[str, float] = {}
liquidity: Dict[str, float] = {}

# Player state
players: Dict[str, Dict] = {}  # name -> {"cash": float, "holdings": {ticker: {"qty": int, "avg": float}}}

# Round / news state
round_no = 0
status = "IDLE"  # IDLE or REACTION
reaction_end_ts: Optional[float] = None
current_news_internal: Optional[Dict] = None
impact_weights: Dict[str, float] = {}        # ticker -> 0..1
reaction_start_ts: Optional[float] = None
reaction_start_price: Dict[str, float] = {}  # ticker -> price at reaction start
rng = random.Random(42)

def _init_market():
    for c in COMPANIES:
        t = c["ticker"]
        sec = c["sector"]
        base_vol = config.BASE_VOL_BY_SECTOR.get(sec, 0.0012)
        vol[t] = max(config.MIN_VOL, base_vol * rng.uniform(0.85, 1.15))
        trend[t] = 0.0
        shock_vol[t] = 0.0
        fair_value[t] = float(c["start_price"])
        liquidity[t] = float(config.LIQUIDITY_BY_SECTOR.get(sec, 8000)) * rng.uniform(0.85, 1.15)

_init_market()

# -------------------- Background thread (Gunicorn/Render safe) --------------------
tick_thread_started = False
tick_thread_lock = threading.Lock()

def background_loop():
    while True:
        time.sleep(config.TICK_SECONDS)
        market_tick()

def ensure_tick_thread():
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
        players[name] = {"cash": float(config.START_CASH), "holdings": {}}

def portfolio_value(name: str) -> Dict:
    ensure_player(name)
    p = players[name]
    cash = p["cash"]
    hv = 0.0
    for t, h in p["holdings"].items():
        hv += prices.get(t, 0.0) * h["qty"]
    total = cash + hv
    return {"cash": cash, "holdings_value": hv, "total_value": total, "holdings": p["holdings"]}

def compute_leaderboard() -> List[Dict]:
    out = []
    for name in list(players.keys()):
        out.append({"player": name, "total": portfolio_value(name)["total_value"]})
    out.sort(key=lambda x: x["total"], reverse=True)
    return out

def public_news(n: Optional[Dict]) -> Optional[Dict]:
    if not n:
        return None
    # Do NOT leak direction/intensity/sectors/tickers
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
        moves.append({"ticker": t, "name": c["name"], "sector": c["sector"], "price": px, "pct": pct})
    moves.sort(key=lambda x: abs(x["pct"]), reverse=True)
    return moves[:n]

# -------------------- Microstructure: bid/ask + slippage --------------------
def quote_bid_ask(ticker: str) -> Tuple[float, float, float]:
    """
    Returns (bid, ask, spread_pct)
    Spread widens with volatility.
    """
    mid = prices[ticker]
    s = config.BASE_SPREAD_PCT + (vol[ticker] * config.SPREAD_VOL_K)
    s = max(config.BASE_SPREAD_PCT, min(0.02, s))  # cap at 2%
    bid = mid * (1.0 - s / 2.0)
    ask = mid * (1.0 + s / 2.0)
    return bid, ask, s

def exec_price(ticker: str, side: str, qty: int) -> Tuple[float, float, float]:
    """
    Returns (fill_price, spread_pct, slippage_pct)
    """
    bid, ask, spread = quote_bid_ask(ticker)
    base_px = ask if side == "BUY" else bid

    liq = max(500.0, liquidity[ticker])
    v = vol[ticker]

    # slippage increases with size and volatility
    slip = (
        config.BASE_SLIP_PCT
        + config.SLIP_QTY_K * (qty / liq) * 10000.0
        + config.SLIP_VOL_K * v
    )
    slip = min(0.05, max(config.BASE_SLIP_PCT, slip))  # cap at 5%

    fill = base_px * (1.0 + slip) if side == "BUY" else base_px * (1.0 - slip)
    return fill, spread, slip

# -------------------- News: jump + trend + vol shock + decay --------------------
def _collect_impacted_tickers(news: Dict) -> Dict[str, float]:
    """
    Returns ticker->weight (0..1) for impact.
    """
    direction = news["direction"]
    sectors = news.get("sectors", []) or []
    tickers = news.get("tickers", []) or []

    sector_set = set(sectors)
    direct = set(tickers)

    # sector-wide if no tickers specified
    if not direct and sector_set:
        for c in COMPANIES:
            if c["sector"] in sector_set:
                direct.add(c["ticker"])

    linked_sectors = set()
    for s in sector_set:
        for ls in config.SECTOR_LINKS.get(s, []):
            linked_sectors.add(ls)

    weights: Dict[str, float] = {}
    for t in prices.keys():
        sec = TICKER_TO_COMPANY[t]["sector"]
        if t in direct:
            weights[t] = config.DIRECT_WEIGHT
        elif sec in sector_set:
            weights[t] = config.SECTOR_WEIGHT
        elif sec in linked_sectors:
            weights[t] = config.LINKED_WEIGHT
        else:
            weights[t] = 0.0

    # Inverse relationships (cost shocks)
    if direction == "UP":
        for src, inv_list in config.SECTOR_INVERSE.items():
            if src in sector_set:
                for t in prices.keys():
                    if TICKER_TO_COMPANY[t]["sector"] in inv_list:
                        # apply a small opposite push
                        weights[t] = max(weights[t], 0.12)

    return weights

def apply_news_effect(news: Dict):
    global status, reaction_end_ts, current_news_internal, round_no

    intensity = news["intensity"]
    direction = news["direction"]

    profile = config.NEWS_PROFILE[intensity]
    jump_lo, jump_hi = profile["jump_pct"]
    tr_lo, tr_hi = profile["trend_per_tick"]
    vb_lo, vb_hi = profile["vol_boost"]

    sign = 1.0 if direction == "UP" else -1.0
    impacted = _collect_impacted_tickers(news)
    global impact_weights, reaction_start_ts, reaction_start_price
    impact_weights = dict(impacted)
    reaction_start_ts = time.time()
    reaction_start_price = {t: prices[t] for t in prices.keys()}

    for t, w in impacted.items():
        if w <= 0.0:
            continue

        jump = rng.uniform(jump_lo, jump_hi) * w * sign
        tr = rng.uniform(tr_lo, tr_hi) * w * sign
        vb = rng.uniform(vb_lo, vb_hi) * w

        # immediate price jump
        prices[t] = max(1.0, prices[t] * (1.0 + jump))

        # push short-term drift and volatility shock
        trend[t] += tr
        shock_vol[t] += vb

    round_no += 1
    current_news_internal = news
    status = "REACTION"
    reaction_end_ts = time.time() + config.REACTION_SECONDS

def end_reaction_if_needed():
    global status, reaction_end_ts, current_news_internal
    if status == "REACTION" and reaction_end_ts is not None and time.time() >= reaction_end_ts:
        status = "IDLE"
        reaction_end_ts = None
        current_news_internal = None
def enforce_min_news_move():
    """
    Ensures impacted tickers visibly move during the reaction window.
    This does NOT reveal direction; it just guarantees magnitude.
    """
    if status != "REACTION" or reaction_end_ts is None or reaction_start_ts is None:
        return

    now = time.time()
    total = max(1.0, float(config.REACTION_SECONDS))
    elapsed = max(0.0, now - reaction_start_ts)
    progress = min(1.0, elapsed / total)

    # Minimum total movement targets by intensity for DIRECT impact
    # (sector/linked get scaled by weight)
    intensity = (current_news_internal or {}).get("intensity", "LOW")
    min_map = {
        "LOW": 0.012,     # 1.2%
        "MEDIUM": 0.025,  # 2.5%
        "HIGH": 0.050,    # 5.0%
    }
    target_total = min_map.get(intensity, 0.012)

    # We want the move to build over time; early ticks smaller, later bigger
    target_so_far = target_total * progress

    for t, w in (impact_weights or {}).items():
        if w <= 0.0:
            continue

        start_px = reaction_start_price.get(t)
        if not start_px:
            continue

        # Required movement magnitude so far (scaled by impact weight)
        req = target_so_far * float(w)

        cur = prices[t]
        cur_move = abs((cur - start_px) / start_px)

        if cur_move < req:
            # Nudge price slightly to meet required visible movement
            # Keep direction based on current trend sign so it looks natural
            direction = 1.0 if trend.get(t, 0.0) >= 0 else -1.0
            gap = req - cur_move
            prices[t] = max(1.0, cur * (1.0 + direction * min(0.0025, gap)))
# -------------------- Market tick: vol clustering + mean reversion + drift --------------------
def market_tick():
    global prev_prices
    with state_lock:
        end_reaction_if_needed()
        prev_prices = dict(prices)

        for t in prices.keys():
            px = prices[t]
            sec = TICKER_TO_COMPANY[t]["sector"]
            base_v = config.BASE_VOL_BY_SECTOR.get(sec, 0.0012)

            # volatility clustering + shock decay
            shock_vol[t] *= config.SHOCK_DECAY
            vol[t] = max(
                config.MIN_VOL,
                (config.VOL_SMOOTH * vol[t]) + ((1.0 - config.VOL_SMOOTH) * base_v) + shock_vol[t]
            )

            # trend decay
            trend[t] *= config.TREND_DECAY

            # mean reversion stronger when calm (low shock)
            fv = fair_value[t]
            if fv > 0:
                mispricing = (px - fv) / fv
                trend[t] -= config.MEAN_REVERT_K * mispricing * max(0.0, 1.0 - min(1.0, shock_vol[t] * 400))

            # random log-return
            eps = rng.gauss(0.0, 1.0)
            r = trend[t] + vol[t] * eps

            # apply move
            px2 = max(1.0, px * math.exp(r))
            prices[t] = px2

            # fair value slowly follows price
            fair_value[t] = (config.FAIR_SMOOTH * fv) + ((1.0 - config.FAIR_SMOOTH) * px2)
        enforce_min_news_move()
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

@app.get("/api/state")
def api_state():
    player = (request.args.get("player") or "").strip()
    with state_lock:
        port = portfolio_value(player) if player else None

        out = {
            "round": round_no,
            "status": status,
            "timer_s": seconds_left(),
            "news": public_news(current_news_internal),
            "prices": prices,                 # mid prices only (UI stays simple)
            "leaderboard": compute_leaderboard(),
            "movers": movers_top(6),
        }
        if port:
            out["portfolio"] = port
        return jsonify(out)

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

        fill, spread, slip = exec_price(ticker, side, qty)
        p = players[player]

        if side == "BUY":
            cost = fill * qty
            if p["cash"] < cost:
                return jsonify({"ok": False, "error": "Not enough cash"}), 400

            p["cash"] -= cost
            h = p["holdings"].get(ticker)
            if not h:
                p["holdings"][ticker] = {"qty": qty, "avg": fill}
            else:
                new_qty = h["qty"] + qty
                new_avg = (h["avg"] * h["qty"] + fill * qty) / new_qty
                h["qty"] = new_qty
                h["avg"] = new_avg

        else:  # SELL
            h = p["holdings"].get(ticker)
            if not h or h["qty"] < qty:
                return jsonify({"ok": False, "error": "Not enough holdings"}), 400

            proceeds = fill * qty
            p["cash"] += proceeds
            h["qty"] -= qty
            if h["qty"] == 0:
                del p["holdings"][ticker]

        return jsonify({
            "ok": True,
            "fill_price": round(fill, 4),
            "spread_pct": round(spread * 100, 4),
            "slip_pct": round(slip * 100, 4),
        })

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
    # admin can see internal fields
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
        for t in prices.keys():
            trend[t] = 0.0
            shock_vol[t] = 0.0
            fair_value[t] = prices[t]
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)