# app.py — News Market Game (realistic-enough market + fun 2-hour pacing)
import json
import math
import random
import threading
import time
from collections import deque
from typing import Dict, Optional, List, Tuple

from flask import Flask, render_template, request, jsonify, redirect
import config

app = Flask(__name__)

# -------------------- Safe config helpers --------------------
def CFG(name: str, default):
    return getattr(config, name, default)

HOST = CFG("HOST", "0.0.0.0")
PORT = int(CFG("PORT", 8000))

START_CASH = float(CFG("START_CASH", 100000))
TICK_SECONDS = float(CFG("TICK_SECONDS", 1.0))
REACTION_SECONDS = int(CFG("REACTION_SECONDS", 45))
CANDLE_SECONDS = int(CFG("CANDLE_SECONDS", 12))  # for OHLC candles used by the UI chart

# Spillover structure
SECTOR_LINKS = CFG("SECTOR_LINKS", {})
SECTOR_INVERSE = CFG("SECTOR_INVERSE", {})  # optional: {"Energy": ["Industrials", ...]} etc.
DIRECT_WEIGHT = float(CFG("DIRECT_WEIGHT", 1.0))
SECTOR_WEIGHT = float(CFG("SECTOR_WEIGHT", 0.35))
LINKED_WEIGHT = float(CFG("LINKED_WEIGHT", 0.18))

# Microstructure (realistic-feel execution)
BASE_SPREAD_PCT = float(CFG("BASE_SPREAD_PCT", 0.0012))  # 0.12%
SPREAD_VOL_K = float(CFG("SPREAD_VOL_K", 6.0))
BASE_SLIP_PCT = float(CFG("BASE_SLIP_PCT", 0.00025))
SLIP_QTY_K = float(CFG("SLIP_QTY_K", 0.015))
SLIP_VOL_K = float(CFG("SLIP_VOL_K", 0.85))
TRADE_FEE_PCT = float(CFG("TRADE_FEE_PCT", 0.0006))  # 0.06% per trade
MIN_FEE = float(CFG("MIN_FEE", 1.0))

# Market dynamics (simple but “market-like”)
BASE_VOL_BY_SECTOR = CFG("BASE_VOL_BY_SECTOR", {})  # optional dict
LIQUIDITY_BY_SECTOR = CFG("LIQUIDITY_BY_SECTOR", {})  # optional dict

MIN_VOL = float(CFG("MIN_VOL", 0.0006))
VOL_SMOOTH = float(CFG("VOL_SMOOTH", 0.92))
SHOCK_DECAY = float(CFG("SHOCK_DECAY", 0.90))
TREND_DECAY = float(CFG("TREND_DECAY", 0.93))
MEAN_REVERT_K = float(CFG("MEAN_REVERT_K", 0.06))
FAIR_SMOOTH = float(CFG("FAIR_SMOOTH", 0.995))

# If config doesn't provide NEWS_PROFILE, we derive a reasonable one from INTENSITY_RANGES
DEFAULT_INTENSITY_RANGES = CFG(
    "INTENSITY_RANGES",
    {"LOW": (0.01, 0.02), "MEDIUM": (0.03, 0.05), "HIGH": (0.06, 0.09)},
)

# -------------------- Data loading --------------------
with open("data/companies.json", "r", encoding="utf-8") as f:
    COMPANIES = json.load(f)

with open("data/news.json", "r", encoding="utf-8") as f:
    NEWS = json.load(f)

TICKER_TO_COMPANY = {c["ticker"]: c for c in COMPANIES}
SECTORS = sorted({c["sector"] for c in COMPANIES})

# -------------------- Shared state --------------------
state_lock = threading.Lock()
rng = random.Random(42)

# Market state per ticker
prices: Dict[str, float] = {c["ticker"]: float(c["start_price"]) for c in COMPANIES}
prev_prices: Dict[str, float] = dict(prices)

# For UI sparklines + chart
price_history: Dict[str, deque] = {
    c["ticker"]: deque([float(c["start_price"])] * 30, maxlen=30) for c in COMPANIES
}
ohlc_history: Dict[str, deque] = {
    c["ticker"]: deque(
        [
            {
                "ts": int(time.time()),
                "o": float(c["start_price"]),
                "h": float(c["start_price"]),
                "l": float(c["start_price"]),
                "c": float(c["start_price"]),
            }
        ],
        maxlen=80,
    )
    for c in COMPANIES
}

# Market dynamics (vol clustering, trends, shocks, fair value)
vol: Dict[str, float] = {}
trend: Dict[str, float] = {}
shock_vol: Dict[str, float] = {}
fair_value: Dict[str, float] = {}
liquidity: Dict[str, float] = {}

# Player state
players: Dict[str, Dict] = {}  # name -> {"cash": float, "holdings": {...}, "trades": [...]}

# Round / news state
round_no = 0
status = "IDLE"  # IDLE or REACTION
reaction_start_ts: Optional[float] = None
reaction_end_ts: Optional[float] = None
current_news_internal: Optional[Dict] = None

impact_weights: Dict[str, float] = {}          # ticker -> weight (0..1)
impact_levels: Dict[str, str] = {}             # ticker -> DIRECT/SECTOR/LINKED/NONE (for presenter only)
reaction_start_price: Dict[str, float] = {}    # ticker -> price at reaction start

# Card theme (for pacing + UI later)
DECK_SUITS = ["♠", "♥", "♦", "♣"]
DECK_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
deck: List[Tuple[str, str]] = []
deck_i = 0
round_card: Optional[Dict] = None


def _reset_deck():
    global deck, deck_i
    deck = [(r, s) for s in DECK_SUITS for r in DECK_RANKS]
    rng.shuffle(deck)
    deck_i = 0


def _draw_card() -> Dict:
    global deck_i
    if not deck:
        _reset_deck()
    if deck_i >= len(deck):
        _reset_deck()
    r, s = deck[deck_i]
    deck_i += 1
    # lightweight “meaning” (used for pacing only, not shown as hints)
    face = r in ("J", "Q", "K")
    ace = r == "A"
    return {"rank": r, "suit": s, "is_face": face, "is_ace": ace}


_reset_deck()


def _init_market():
    for c in COMPANIES:
        t = c["ticker"]
        sec = c["sector"]
        base_v = float(BASE_VOL_BY_SECTOR.get(sec, 0.0012))
        vol[t] = max(MIN_VOL, base_v * rng.uniform(0.85, 1.15))
        trend[t] = 0.0
        shock_vol[t] = 0.0
        fair_value[t] = float(c["start_price"])
        liquidity[t] = float(LIQUIDITY_BY_SECTOR.get(sec, 8000.0)) * rng.uniform(0.85, 1.15)


_init_market()

# -------------------- Background thread (Gunicorn/Render safe) --------------------
tick_thread_started = False
tick_thread_lock = threading.Lock()


def background_loop():
    while True:
        time.sleep(TICK_SECONDS)
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
        players[name] = {"cash": float(START_CASH), "holdings": {}, "trades": []}


def portfolio_value(name: str) -> Dict:
    ensure_player(name)
    p = players[name]
    cash = p["cash"]
    hv = 0.0
    for t, h in p["holdings"].items():
        hv += prices.get(t, 0.0) * h["qty"]
    total = cash + hv
    return {
        "cash": cash,
        "holdings_value": hv,
        "total_value": total,
        "holdings": p["holdings"],
        "recent_trades": (p.get("trades") or [])[-8:],
    }


def compute_leaderboard() -> List[Dict]:
    out = []
    for name in list(players.keys()):
        out.append({"player": name, "total": portfolio_value(name)["total_value"]})
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


def public_news(n: Optional[Dict]) -> Optional[Dict]:
    if not n:
        return None
    # IMPORTANT: do NOT leak direction/intensity/sectors/tickers to players.
    # We can safely include the round card later for UI theme.
    return {
        "id": n.get("id"),
        "headline": n.get("headline"),
        "summary": n.get("summary"),
        "body": n.get("body"),
        "bullets": n.get("bullets") or [],
        "card": round_card,  # harmless theme
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


def reaction_meta() -> Dict:
    if status != "REACTION" or reaction_start_ts is None or reaction_end_ts is None:
        return {"active": False, "pulse": "CALM", "affected": 0, "progress": 0}

    affected = sum(1 for w in impact_weights.values() if w > 0)
    # pulse based on average shock+trend magnitude on impacted tickers
    mags = []
    for t, w in impact_weights.items():
        if w <= 0:
            continue
        mags.append(abs(trend.get(t, 0.0)) + shock_vol.get(t, 0.0))
    mean_mag = (sum(mags) / max(1, len(mags))) if mags else 0.0

    pulse = "CALM"
    if mean_mag >= 0.0025:
        pulse = "HIGH"
    elif mean_mag >= 0.0012:
        pulse = "MEDIUM"

    elapsed = max(0.0, time.time() - reaction_start_ts)
    progress = min(100, int((elapsed / max(REACTION_SECONDS, 1)) * 100))
    return {"active": True, "pulse": pulse, "affected": affected, "progress": progress}


# -------------------- Microstructure: bid/ask + slippage --------------------
def quote_bid_ask(ticker: str) -> Tuple[float, float, float]:
    """Returns (bid, ask, spread_pct). Spread widens with volatility."""
    mid = float(prices[ticker])
    s = BASE_SPREAD_PCT + (float(vol.get(ticker, 0.0012)) * SPREAD_VOL_K)
    s = max(BASE_SPREAD_PCT, min(0.02, s))  # cap at 2%
    bid = mid * (1.0 - s / 2.0)
    ask = mid * (1.0 + s / 2.0)
    return bid, ask, s


def exec_price(ticker: str, side: str, qty: int) -> Tuple[float, float, float]:
    """Returns (fill_price, spread_pct, slippage_pct)."""
    bid, ask, spread = quote_bid_ask(ticker)
    base_px = ask if side == "BUY" else bid

    liq = max(500.0, float(liquidity.get(ticker, 8000.0)))
    v = float(vol.get(ticker, 0.0012))

    # slippage increases with size and volatility; kept simple
    slip = BASE_SLIP_PCT + SLIP_QTY_K * (qty / liq) + SLIP_VOL_K * v
    slip = min(0.05, max(BASE_SLIP_PCT, slip))  # cap at 5%

    fill = base_px * (1.0 + slip) if side == "BUY" else base_px * (1.0 - slip)
    return float(fill), float(spread), float(slip)


def quotes_for_all() -> Dict[str, Dict]:
    out = {}
    for t in prices.keys():
        bid, ask, sp = quote_bid_ask(t)
        out[t] = {
            "bid": bid,
            "ask": ask,
            "spread_pct": sp,
        }
    return out


# -------------------- News impact model (jump + trend + vol shock) --------------------
def _news_profile(intensity: str) -> Dict:
    """
    Returns dict with:
      jump_pct: (lo, hi)
      trend_per_tick: (lo, hi)
      vol_boost: (lo, hi)
    """
    if hasattr(config, "NEWS_PROFILE"):
        prof = getattr(config, "NEWS_PROFILE")
        if intensity in prof:
            return prof[intensity]

    # derive from intensity ranges (total move) if NEWS_PROFILE not present
    lo, hi = DEFAULT_INTENSITY_RANGES.get(intensity, (0.01, 0.02))
    ticks = max(1, int(REACTION_SECONDS / max(TICK_SECONDS, 0.25)))

    # immediate “gap” + sustained drift; tuned to look obvious vs normal noise
    jump_pct = (lo * 0.25, hi * 0.45)
    drift_total = (lo * 0.25, hi * 0.40)
    trend_per_tick = (drift_total[0] / ticks, drift_total[1] / ticks)

    # volatility shock controls “choppiness” during reaction
    if intensity == "HIGH":
        vol_boost = (0.0012, 0.0025)
    elif intensity == "MEDIUM":
        vol_boost = (0.0007, 0.0016)
    else:
        vol_boost = (0.00035, 0.0009)

    return {"jump_pct": jump_pct, "trend_per_tick": trend_per_tick, "vol_boost": vol_boost}


def _collect_impacted_tickers(news: Dict) -> Tuple[Dict[str, float], Dict[str, str]]:
    """
    Returns:
      weights: ticker -> weight (0..1)
      levels: ticker -> DIRECT/SECTOR/LINKED/NONE   (direction-free)
    """
    sectors = news.get("sectors", []) or []
    tickers = news.get("tickers", []) or []

    sector_set = set(sectors)
    direct = set(tickers)

    # If sector news has no direct tickers, affect all companies in that sector.
    if not direct and sector_set:
        for c in COMPANIES:
            if c["sector"] in sector_set:
                direct.add(c["ticker"])

    linked_sectors = set()
    for s in sector_set:
        for ls in SECTOR_LINKS.get(s, []) or []:
            linked_sectors.add(ls)

    weights: Dict[str, float] = {}
    levels: Dict[str, str] = {}

    for t in prices.keys():
        sec = TICKER_TO_COMPANY[t]["sector"]
        if t in direct:
            weights[t] = DIRECT_WEIGHT
            levels[t] = "DIRECT"
        elif sec in sector_set:
            weights[t] = SECTOR_WEIGHT
            levels[t] = "SECTOR"
        elif sec in linked_sectors:
            weights[t] = LINKED_WEIGHT
            levels[t] = "LINKED"
        else:
            weights[t] = 0.0
            levels[t] = "NONE"

    # Optional inverse spillovers (cost shocks), direction handled later
    if (news.get("direction") or "").upper() == "UP" and sector_set:
        for src, inv_list in (SECTOR_INVERSE or {}).items():
            if src in sector_set:
                for t in prices.keys():
                    if TICKER_TO_COMPANY[t]["sector"] in (inv_list or []):
                        # direction-free weight marker; actual sign handled in apply_news_effect
                        if levels[t] == "NONE":
                            levels[t] = "LINKED"
                        weights[t] = max(weights[t], 0.12)

    return weights, levels


def _card_multiplier(card: Optional[Dict]) -> Dict[str, float]:
    """
    Subtle pacing lever.
    Does NOT reveal market direction; only changes how dramatic/choppy it feels.
    """
    if not card:
        return {"jump": 1.0, "trend": 1.0, "vol": 1.0}
    if card.get("is_ace"):
        return {"jump": 1.25, "trend": 1.15, "vol": 1.35}  # “Ace = headline shock”
    if card.get("is_face"):
        return {"jump": 1.15, "trend": 1.10, "vol": 1.20}  # “Face = drama”
    # numeric cards: calmer
    return {"jump": 1.0, "trend": 1.0, "vol": 1.0}


def apply_news_effect(news: Dict):
    """
    Applies a visible, meaningful reaction:
      - immediate gap move on impacted tickers
      - sustained drift during reaction window
      - extra volatility (choppy moves)
    """
    global status, reaction_start_ts, reaction_end_ts, current_news_internal
    global round_no, impact_weights, impact_levels, reaction_start_price, round_card

    intensity = (news.get("intensity") or "LOW").upper()
    direction = (news.get("direction") or "UP").upper()
    sign = 1.0 if direction == "UP" else -1.0

    # Theme/pacing
    round_card = _draw_card()
    cm = _card_multiplier(round_card)

    profile = _news_profile(intensity)
    jump_lo, jump_hi = profile["jump_pct"]
    tr_lo, tr_hi = profile["trend_per_tick"]
    vb_lo, vb_hi = profile["vol_boost"]

    weights, levels = _collect_impacted_tickers(news)
    impact_weights = dict(weights)
    impact_levels = dict(levels)

    reaction_start_ts = time.time()
    reaction_end_ts = reaction_start_ts + REACTION_SECONDS
    reaction_start_price = {t: float(prices[t]) for t in prices.keys()}

    # Apply effect
    for t, w in impact_weights.items():
        if w <= 0.0:
            continue

        # big visible difference versus normal drift/noise
        jump = rng.uniform(jump_lo, jump_hi) * w * sign * cm["jump"]
        tr = rng.uniform(tr_lo, tr_hi) * w * sign * cm["trend"]
        vb = rng.uniform(vb_lo, vb_hi) * w * cm["vol"]

        prices[t] = max(1.0, float(prices[t]) * (1.0 + jump))
        trend[t] += tr
        shock_vol[t] += vb

    round_no += 1
    current_news_internal = news
    status = "REACTION"


def end_reaction_if_needed():
    global status, reaction_start_ts, reaction_end_ts, current_news_internal
    if status == "REACTION" and reaction_end_ts is not None and time.time() >= reaction_end_ts:
        status = "IDLE"
        reaction_start_ts = None
        reaction_end_ts = None
        current_news_internal = None
        # accelerate decay a bit after the window ends
        for t in prices.keys():
            trend[t] *= 0.7
            shock_vol[t] *= 0.7


def enforce_min_news_move():
    """
    Guarantee: impacted tickers move *noticeably* during the reaction window.
    This keeps the game feeling “news-driven” even with randomness.
    Direction is NOT leaked: we follow the current trend sign.
    """
    if status != "REACTION" or reaction_start_ts is None or reaction_end_ts is None:
        return

    now = time.time()
    total = max(1.0, float(REACTION_SECONDS))
    elapsed = max(0.0, now - reaction_start_ts)
    progress = min(1.0, elapsed / total)

    intensity = ((current_news_internal or {}).get("intensity") or "LOW").upper()
    min_map = {"LOW": 0.012, "MEDIUM": 0.028, "HIGH": 0.055}  # target total move (DIRECT)
    target_total = float(min_map.get(intensity, 0.012))
    target_so_far = target_total * progress

    for t, w in impact_weights.items():
        if w <= 0.0:
            continue
        start_px = reaction_start_price.get(t)
        if not start_px:
            continue

        req = target_so_far * float(w)
        cur = float(prices[t])
        cur_move = abs((cur - start_px) / start_px)

        if cur_move < req:
            direction = 1.0 if float(trend.get(t, 0.0)) >= 0 else -1.0
            gap = req - cur_move
            prices[t] = max(1.0, cur * (1.0 + direction * min(0.0030, gap)))


# -------------------- Market tick (main loop) --------------------
def _update_histories(ticker: str, px: float):
    # sparkline history
    price_history[ticker].append(px)

    # ohlc candle aggregation
    now_ts = int(time.time())
    candle = ohlc_history[ticker][-1]
    if now_ts - int(candle["ts"]) >= CANDLE_SECONDS:
        ohlc_history[ticker].append({"ts": now_ts, "o": px, "h": px, "l": px, "c": px})
    else:
        candle["h"] = max(float(candle["h"]), px)
        candle["l"] = min(float(candle["l"]), px)
        candle["c"] = px


def market_tick():
    global prev_prices
    with state_lock:
        end_reaction_if_needed()
        prev_prices = dict(prices)

        for t in list(prices.keys()):
            px = float(prices[t])
            sec = TICKER_TO_COMPANY[t]["sector"]

            base_v = float(BASE_VOL_BY_SECTOR.get(sec, 0.0012))

            # volatility clustering + shock decay
            shock_vol[t] *= SHOCK_DECAY
            vol[t] = max(
                MIN_VOL,
                (VOL_SMOOTH * float(vol[t]))
                + ((1.0 - VOL_SMOOTH) * base_v)
                + float(shock_vol[t]),
            )

            # trend decay
            trend[t] *= TREND_DECAY

            # mean reversion (weaker during high shock)
            fv = float(fair_value[t])
            if fv > 0:
                mispricing = (px - fv) / fv
                calm_factor = max(0.0, 1.0 - min(1.0, float(shock_vol[t]) * 420.0))
                trend[t] -= MEAN_REVERT_K * mispricing * calm_factor

            # random log-return
            eps = rng.gauss(0.0, 1.0)
            r = float(trend[t]) + float(vol[t]) * eps

            px2 = max(1.0, px * math.exp(r))
            prices[t] = px2

            # fair value follows slowly
            fair_value[t] = (FAIR_SMOOTH * fv) + ((1.0 - FAIR_SMOOTH) * px2)

            _update_histories(t, px2)

        # ensure visible reaction movement after applying base tick
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


@app.get("/api/latest_state")
def api_state():
    player = (request.args.get("player") or "").strip()
    with state_lock:
        port = portfolio_value(player) if player else None

        # Players should NOT get an impact map (avoid hints). Presenter can.
        if player:
            safe_impact = {t: "NONE" for t in prices.keys()}
        else:
            safe_impact = dict(impact_levels) if impact_levels else {t: "NONE" for t in prices.keys()}

        out = {
            "round": round_no,
            "status": status,
            "timer_s": seconds_left(),
            "news": public_news(current_news_internal),
            "prices": prices,  # mid/mark price for UI simplicity
            "leaderboard": compute_leaderboard(),
            "movers": movers_top(6),
            "reaction_meta": reaction_meta(),
            "impact_map": safe_impact,
            "quotes": quotes_for_all(),
            "history": {t: list(h) for t, h in price_history.items()},
            "ohlc": {t: list(h) for t, h in ohlc_history.items()},
        }
        if port:
            out["portfolio"] = port
        return jsonify(out)


# Keep old route name too (frontend uses /api/state)
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
        p = players[player]

        fill, spread, slip = exec_price(ticker, side, qty)
        notional = fill * qty
        fee = max(MIN_FEE, notional * TRADE_FEE_PCT)

        if side == "BUY":
            cost = notional + fee
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

            proceeds = notional - fee
            p["cash"] += proceeds
            h["qty"] -= qty
            if h["qty"] == 0:
                del p["holdings"][ticker]

        # log trade (for UI)
        p["trades"].append(
            {
                "ts": int(time.time()),
                "ticker": ticker,
                "side": side,
                "qty": qty,
                "fill": round(fill, 4),
                "fee": round(fee, 2),
            }
        )

        return jsonify(
            {
                "ok": True,
                "fill_price": round(fill, 4),
                "spread_pct": round(spread * 100, 4),
                "slip_pct": round(slip * 100, 4),
                "fee": round(fee, 2),
            }
        )


# -------- Admin APIs --------
@app.post("/api/admin/login")
def api_admin_login():
    data = request.get_json(force=True, silent=True) or {}
    if (data.get("password") or "") == CFG("ADMIN_PASSWORD", "admin123"):
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401


@app.get("/api/admin/state")
def api_admin_state():
    with state_lock:
        return jsonify(
            {
                "round": round_no,
                "status": status,
                "timer_s": seconds_left(),
                "headline": (current_news_internal or {}).get("headline"),
            }
        )


@app.get("/api/admin/news")
def api_admin_news():
    # Admin can see internal fields (direction/intensity/etc) for running the game.
    return jsonify({"news": NEWS})


def check_admin(password: str) -> bool:
    return password == CFG("ADMIN_PASSWORD", "admin123")


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

    global prices, prev_prices, players, round_no, status, reaction_start_ts, reaction_end_ts
    global current_news_internal, impact_weights, impact_levels, reaction_start_price, round_card

    with state_lock:
        prices = {c["ticker"]: float(c["start_price"]) for c in COMPANIES}
        prev_prices = dict(prices)

        # reset histories
        for c in COMPANIES:
            t = c["ticker"]
            sp = float(c["start_price"])
            price_history[t].clear()
            price_history[t].extend([sp] * 30)
            ohlc_history[t].clear()
            ohlc_history[t].append({"ts": int(time.time()), "o": sp, "h": sp, "l": sp, "c": sp})

        # reset market model
        for t in prices.keys():
            trend[t] = 0.0
            shock_vol[t] = 0.0
            fair_value[t] = float(prices[t])

        players = {}
        round_no = 0
        status = "IDLE"
        reaction_start_ts = None
        reaction_end_ts = None
        current_news_internal = None
        impact_weights = {}
        impact_levels = {}
        reaction_start_price = {}
        round_card = None
        _reset_deck()

    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False, threaded=True)