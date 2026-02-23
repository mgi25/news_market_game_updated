import json
import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from flask import Flask, jsonify, redirect, render_template, request

import config

app = Flask(__name__)

# =========================
# Load data (path-safe)
# =========================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

with open(DATA_DIR / "companies.json", "r", encoding="utf-8") as f:
    COMPANIES = json.load(f)

with open(DATA_DIR / "news.json", "r", encoding="utf-8") as f:
    NEWS = json.load(f)

TICKER_TO_COMPANY = {c["ticker"]: c for c in COMPANIES}
TICKERS = [c["ticker"] for c in COMPANIES]
SECTORS = sorted({c.get("sector", "Other") for c in COMPANIES})

# =========================
# Config (safe defaults)
# =========================
TICK_SECONDS = float(getattr(config, "TICK_SECONDS", 1.0))

EVENT_TOTAL_MINUTES = int(getattr(config, "EVENT_TOTAL_MINUTES", 90))
DAY_SECONDS_DEFAULT = int(getattr(config, "DAY_SECONDS", 150))
DAY_SECONDS_MIN = int(getattr(config, "DAY_SECONDS_MIN", 0))
DAY_SECONDS_MAX = int(getattr(config, "DAY_SECONDS_MAX", 0))

P_NO_NEWS = float(getattr(config, "P_NO_NEWS", 0.08))
P_TWO_NEWS = float(getattr(config, "P_TWO_NEWS", 0.25))
P_THREE_NEWS = float(getattr(config, "P_THREE_NEWS", 0.05))
MAX_NEWS_PER_DAY = int(getattr(config, "MAX_NEWS_PER_DAY", 3))

REACTION_SECONDS = int(getattr(config, "REACTION_SECONDS", 18))

START_CASH = float(getattr(config, "START_CASH", 100000.0))
FEE_BPS = float(getattr(config, "FEE_BPS", 2.0))
BASE_SPREAD_BPS = float(getattr(config, "BASE_SPREAD_BPS", 8.0))
VOL_SPREAD_BPS = float(getattr(config, "VOL_SPREAD_BPS", 35.0))

BASE_INTRADAY_VOL = float(getattr(config, "BASE_INTRADAY_VOL", 0.0010))
MARKET_BETA_RANGE = getattr(config, "MARKET_BETA_RANGE", (0.6, 1.2))
SECTOR_BETA_RANGE = getattr(config, "SECTOR_BETA_RANGE", (0.3, 0.9))
IDIO_WEIGHT = float(getattr(config, "IDIO_WEIGHT", 0.6))

# Smoother returns
NOISE_AR_PHI = float(getattr(config, "NOISE_AR_PHI", 0.88))

# UI move is over a window (less jumpy than last tick)
MOVE_LOOKBACK_SECONDS = int(getattr(config, "MOVE_LOOKBACK_SECONDS", 12))

NEWS_IMPACT_PCT = getattr(
    config,
    "NEWS_IMPACT_PCT",
    {
        "LOW": (0.006, 0.012),
        "MEDIUM": (0.012, 0.025),
        "HIGH": (0.025, 0.055),
    },
)

DIRECT_WEIGHT = float(getattr(config, "DIRECT_WEIGHT", 1.00))
SECTOR_WEIGHT = float(getattr(config, "SECTOR_WEIGHT", 0.55))
LINKED_WEIGHT = float(getattr(config, "LINKED_WEIGHT", 0.25))

SECTOR_LINKS = getattr(config, "SECTOR_LINKS", {})
ADMIN_PASSWORD = str(getattr(config, "ADMIN_PASSWORD", "admin")).strip()
CANDLE_SECONDS = int(getattr(config, "CANDLE_SECONDS", 5))

# =========================
# Shared State
# =========================
state_lock = threading.Lock()

prices: Dict[str, float] = {c["ticker"]: float(c["start_price"]) for c in COMPANIES}
prev_prices: Dict[str, float] = dict(prices)

# Make history long enough for rolling-window moves
HIST_SECONDS = max(40, MOVE_LOOKBACK_SECONDS * 6)
HIST_MAXLEN = max(120, int(HIST_SECONDS / max(0.001, TICK_SECONDS)))

price_history: Dict[str, deque] = {
    t: deque([prices[t]] * min(60, HIST_MAXLEN), maxlen=HIST_MAXLEN) for t in TICKERS
}

ohlc_history: Dict[str, deque] = {
    t: deque([{
        "ts": int(time.time()),
        "o": prices[t],
        "h": prices[t],
        "l": prices[t],
        "c": prices[t]
    }], maxlen=160) for t in TICKERS
}

players: Dict[str, Dict] = {}

event_running: bool = False
event_seed: int = 0
event_start_ts: float = 0.0
event_end_ts: float = 0.0

day_no: int = 0
total_days: int = 0

global_tick: int = 0
total_ticks: int = 0

day_boundaries: List[Tuple[int, int]] = []
current_day_start_tick: int = 0
current_day_end_tick: int = 0

status: str = "IDLE"  # IDLE or REACTION
current_news_internal: Optional[Dict] = None
current_impact_map: Dict[str, str] = {}
reaction_end_tick: int = 0

# Deterministic factors
market_z: List[float] = []
sector_z: Dict[str, List[float]] = {}
idio_z: Dict[str, List[float]] = {}
market_beta: Dict[str, float] = {}
sector_beta: Dict[str, float] = {}
liquidity_units: Dict[str, float] = {}

# Smooth AR states
market_ar_state: float = 0.0
sector_ar_state: Dict[str, float] = {s: 0.0 for s in SECTORS}
idio_ar_state: Dict[str, float] = {t: 0.0 for t in TICKERS}

# Day targets
day_open_price: Dict[str, float] = {}
base_day_close_target: Dict[str, float] = {}   # baseline (before news)
day_close_target: Dict[str, float] = {}        # effective (after news)
day_target_mult: Dict[str, float] = {t: 1.0 for t in TICKERS}  # cumulative news multiplier

# drift / vol multipliers
day_drift_add: Dict[str, float] = {t: 0.0 for t in TICKERS}
reaction_drift_add: Dict[str, float] = {t: 0.0 for t in TICKERS}
day_vol_mult: Dict[str, float] = {t: 1.0 for t in TICKERS}
reaction_vol_mult: Dict[str, float] = {t: 1.0 for t in TICKERS}


@dataclass
class ScheduledNews:
    tick: int
    news: Dict
    day_index: int
    impact_total_pct: float
    impact_map: Dict[str, str]


scheduled_news: List[ScheduledNews] = []

# =========================
# Background thread
# =========================
tick_thread_started = False
tick_thread_lock = threading.Lock()


def ensure_tick_thread():
    global tick_thread_started
    if tick_thread_started:
        return
    with tick_thread_lock:
        if tick_thread_started:
            return
        t = threading.Thread(target=_background_loop, daemon=True)
        t.start()
        tick_thread_started = True


def _background_loop():
    while True:
        time.sleep(TICK_SECONDS)
        try:
            market_tick()
        except Exception:
            pass


@app.before_request
def _before_any_request():
    ensure_tick_thread()


# =========================
# Helpers
# =========================
def ensure_player(player: str):
    if player not in players:
        players[player] = {"cash": float(START_CASH), "holdings": {}, "trades": []}


def holdings_value(player: str) -> float:
    p = players.get(player)
    if not p:
        return 0.0
    total = 0.0
    for t, h in p["holdings"].items():
        total += float(prices.get(t, 0.0)) * int(h["qty"])
    return total


def portfolio(player: str) -> Dict:
    ensure_player(player)
    p = players[player]
    hv = holdings_value(player)
    return {
        "player": player,
        "cash": round(p["cash"], 2),
        "holdings_value": round(hv, 2),
        "equity": round(p["cash"] + hv, 2),
        "holdings": p["holdings"],
        "trades": p["trades"][-30:],
    }


def compute_leaderboard() -> List[Dict]:
    rows = []
    for name in players.keys():
        pf = portfolio(name)
        rows.append({"player": name, "equity": pf["equity"], "cash": pf["cash"]})
    rows.sort(key=lambda x: x["equity"], reverse=True)
    return rows[:20]


def _intraday_vol_curve(pos: float) -> float:
    open_bump = math.exp(-pos / 0.14)
    close_bump = math.exp(-(1.0 - pos) / 0.14)
    return 0.65 + 0.85 * (open_bump + close_bump)


def _current_day_index() -> int:
    for i, (a, b) in enumerate(day_boundaries, start=1):
        if a <= global_tick <= b:
            return i
    return max(1, day_no)


def _set_current_day_bounds(day_index: int):
    global current_day_start_tick, current_day_end_tick
    if day_index <= 0 or day_index > len(day_boundaries):
        current_day_start_tick, current_day_end_tick = 0, max(0, total_ticks - 1)
        return
    a, b = day_boundaries[day_index - 1]
    current_day_start_tick, current_day_end_tick = a, b


def _day_pos() -> float:
    if current_day_end_tick <= current_day_start_tick:
        return 0.0
    return (global_tick - current_day_start_tick) / float(current_day_end_tick - current_day_start_tick)


def _current_spread_bps(ticker: str) -> float:
    vm = max(1.0, day_vol_mult.get(ticker, 1.0), reaction_vol_mult.get(ticker, 1.0))
    return BASE_SPREAD_BPS + (vm - 1.0) * VOL_SPREAD_BPS


def quotes_for_all() -> Dict[str, Dict]:
    q = {}
    for t in TICKERS:
        mid = float(prices[t])
        spread_bps = _current_spread_bps(t)
        spread = mid * (spread_bps / 10000.0)
        bid = max(0.01, mid - spread / 2.0)
        ask = max(0.01, mid + spread / 2.0)
        q[t] = {"mid": mid, "bid": bid, "ask": ask, "spread_bps": spread_bps}
    return q


def _pick_day_seconds(rng: random.Random) -> int:
    if DAY_SECONDS_MIN > 0 and DAY_SECONDS_MAX > DAY_SECONDS_MIN:
        return rng.randint(DAY_SECONDS_MIN, DAY_SECONDS_MAX)
    return DAY_SECONDS_DEFAULT


def _news_intensity_range(intensity: str) -> Tuple[float, float]:
    k = (intensity or "MEDIUM").upper()
    if k == "MID":
        k = "MEDIUM"
    if k not in NEWS_IMPACT_PCT:
        k = "MEDIUM"
    lo, hi = NEWS_IMPACT_PCT[k]
    return float(lo), float(hi)


def _build_impact_map(news_obj: Dict) -> Dict[str, str]:
    direct = set(news_obj.get("tickers", []) or [])
    sectors = set(news_obj.get("sectors", []) or [])

    linked_sectors = set()
    for s in sectors:
        for ls in (SECTOR_LINKS.get(s) or []):
            linked_sectors.add(ls)

    mp: Dict[str, str] = {}
    for t in TICKERS:
        sec = TICKER_TO_COMPANY[t].get("sector", "Other")
        if t in direct:
            mp[t] = "DIRECT"
        elif sec in sectors:
            mp[t] = "SECTOR"
        elif sec in linked_sectors:
            mp[t] = "LINKED"
        else:
            mp[t] = "NONE"
    return mp


def _choose_news_for_day(rng: random.Random, used_ids: set) -> Dict:
    for _ in range(12):
        n = rng.choice(NEWS)
        nid = n.get("id")
        if nid and nid not in used_ids:
            return n
    return rng.choice(NEWS)


def _news_count_for_day(rng: random.Random) -> int:
    r = rng.random()
    if r < P_NO_NEWS:
        return 0
    r2 = rng.random()
    if r2 < P_THREE_NEWS:
        return min(3, MAX_NEWS_PER_DAY)
    if r2 < P_TWO_NEWS:
        return min(2, MAX_NEWS_PER_DAY)
    return 1


def build_event_plan(seed: int):
    global day_boundaries, scheduled_news
    global total_ticks, total_days
    global market_z, sector_z, idio_z, market_beta, sector_beta, liquidity_units

    rng = random.Random(seed)

    total_seconds = EVENT_TOTAL_MINUTES * 60
    total_ticks_local = int(total_seconds / max(0.001, TICK_SECONDS))
    total_ticks_local = max(total_ticks_local, 10)

    boundaries: List[Tuple[int, int]] = []
    tick_cursor = 0
    while tick_cursor < total_ticks_local:
        ds = _pick_day_seconds(rng)
        dticks = max(10, int(ds / max(0.001, TICK_SECONDS)))
        a = tick_cursor
        b = min(total_ticks_local - 1, tick_cursor + dticks - 1)
        boundaries.append((a, b))
        tick_cursor = b + 1

    def n01():
        u1 = max(1e-9, rng.random())
        u2 = max(1e-9, rng.random())
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    market_z_local = [n01() for _ in range(total_ticks_local)]
    sector_z_local: Dict[str, List[float]] = {s: [n01() for _ in range(total_ticks_local)] for s in SECTORS}
    idio_z_local: Dict[str, List[float]] = {t: [n01() for _ in range(total_ticks_local)] for t in TICKERS}

    mb_lo, mb_hi = MARKET_BETA_RANGE
    sb_lo, sb_hi = SECTOR_BETA_RANGE

    market_beta_local = {}
    sector_beta_local = {}
    liquidity_local = {}

    for t in TICKERS:
        market_beta_local[t] = mb_lo + (mb_hi - mb_lo) * rng.random()
        sector_beta_local[t] = sb_lo + (sb_hi - sb_lo) * rng.random()
        sp = float(TICKER_TO_COMPANY[t]["start_price"])
        liquidity_local[t] = (5000.0 + 20000.0 * rng.random()) * (1.0 + sp / 200.0)

    used_ids = set()
    news_sched: List[ScheduledNews] = []

    for day_idx, (a, b) in enumerate(boundaries, start=1):
        day_ticks = b - a + 1
        if day_ticks < 10:
            continue

        k = _news_count_for_day(rng)
        if k <= 0:
            continue

        min_gap = max(6, int(REACTION_SECONDS / max(0.001, TICK_SECONDS)) + 4)
        candidate_ticks = []
        tries = 0
        while len(candidate_ticks) < k and tries < 200:
            tries += 1
            lo = a + max(2, int(day_ticks * 0.10))
            hi = b - max(2, int(day_ticks * 0.12))
            if hi <= lo:
                lo, hi = a + 2, b - 2
            if hi <= lo:
                break
            t0 = rng.randint(lo, hi)
            if all(abs(t0 - x) >= min_gap for x in candidate_ticks):
                candidate_ticks.append(t0)

        candidate_ticks.sort()

        for t0 in candidate_ticks[:k]:
            nobj = _choose_news_for_day(rng, used_ids)
            if nobj.get("id"):
                used_ids.add(nobj["id"])

            mp = _build_impact_map(nobj)

            lo, hi = _news_intensity_range(nobj.get("intensity", "MEDIUM"))
            magnitude = lo + (hi - lo) * rng.random()
            direction = (nobj.get("direction", "UP") or "UP").upper()
            sign = 1.0 if direction == "UP" else -1.0

            news_sched.append(
                ScheduledNews(
                    tick=t0,
                    news=nobj,
                    day_index=day_idx,
                    impact_total_pct=sign * magnitude,
                    impact_map=mp,
                )
            )

    news_sched.sort(key=lambda x: x.tick)

    day_boundaries = boundaries
    scheduled_news = news_sched
    total_ticks = total_ticks_local
    total_days = len(boundaries)

    market_z = market_z_local
    sector_z = sector_z_local
    idio_z = idio_z_local
    market_beta = market_beta_local
    sector_beta = sector_beta_local
    liquidity_units = liquidity_local


def _start_new_day(day_index: int):
    global day_no, day_open_price, base_day_close_target, day_close_target
    global current_news_internal, current_impact_map, status, reaction_end_tick

    day_no = day_index
    _set_current_day_bounds(day_index)

    for t in TICKERS:
        day_drift_add[t] = 0.0
        reaction_drift_add[t] = 0.0
        day_vol_mult[t] = 1.0
        reaction_vol_mult[t] = 1.0
        day_target_mult[t] = 1.0

    current_news_internal = None
    current_impact_map = {}
    status = "IDLE"
    reaction_end_tick = 0

    day_open_price = {t: float(prices[t]) for t in TICKERS}
    base_day_close_target = {}
    day_close_target = {}

    rng = random.Random(event_seed + day_index * 999)

    def clamp(x, lo, hi):
        return max(lo, min(hi, x))

    market_theme = rng.uniform(-0.004, 0.004)
    sector_theme: Dict[str, float] = {s: rng.uniform(-0.003, 0.003) for s in SECTORS}

    for t in TICKERS:
        sec = TICKER_TO_COMPANY[t].get("sector", "Other")
        idio = rng.uniform(-0.004, 0.004)
        day_ret = market_theme * market_beta[t] + sector_theme.get(sec, 0.0) * sector_beta[t] + 0.6 * idio
        day_ret = clamp(day_ret, -0.012, 0.012)
        base = day_open_price[t] * (1.0 + day_ret)
        base_day_close_target[t] = base
        day_close_target[t] = base


def apply_scheduled_news(sn: ScheduledNews):
    """
    FIXED: news now shifts end-of-day target (direction must win),
    while still creating a reaction spike + day-long trend.
    """
    global current_news_internal, current_impact_map, status, reaction_end_tick

    current_news_internal = sn.news
    current_impact_map = sn.impact_map
    status = "REACTION"

    reaction_ticks = max(3, int(REACTION_SECONDS / max(0.001, TICK_SECONDS)))
    reaction_end_tick = min(current_day_end_tick, global_tick + reaction_ticks)

    total = sn.impact_total_pct

    # Split:
    # - reaction spike: fast visible move
    # - day influence: mostly via target shift + small drift
    shock_part = 0.45 * total
    target_part = 0.45 * total   # used to shift day close target
    drift_part = 0.10 * total    # small persistent drift

    remaining_day_ticks = max(1, current_day_end_tick - global_tick + 1)
    shock_ticks = max(1, reaction_end_tick - global_tick + 1)

    shock_per_tick = shock_part / shock_ticks
    drift_per_tick = drift_part / remaining_day_ticks

    for t in TICKERS:
        kind = sn.impact_map.get(t, "NONE")
        if kind == "DIRECT":
            w = DIRECT_WEIGHT
            reaction_vol_mult[t] = max(reaction_vol_mult[t], 3.0)
            day_vol_mult[t] = max(day_vol_mult[t], 1.65)
        elif kind == "SECTOR":
            w = SECTOR_WEIGHT
            reaction_vol_mult[t] = max(reaction_vol_mult[t], 2.2)
            day_vol_mult[t] = max(day_vol_mult[t], 1.35)
        elif kind == "LINKED":
            w = LINKED_WEIGHT
            reaction_vol_mult[t] = max(reaction_vol_mult[t], 1.6)
            day_vol_mult[t] = max(day_vol_mult[t], 1.15)
        else:
            continue

        # Reaction spike + small day drift
        reaction_drift_add[t] += w * shock_per_tick
        day_drift_add[t] += w * drift_per_tick

        # *** Key fix: shift end-of-day target in news direction ***
        # Apply cumulative multiplier on baseline target
        adj = w * target_part
        day_target_mult[t] *= (1.0 + adj)
        base = float(base_day_close_target.get(t, prices[t]))
        day_close_target[t] = base * day_target_mult[t]

        # Enforce direction vs current price so "good news can't end up pulling down"
        cur = float(prices[t])
        floor_strength = 0.55  # ensures direction is visible even if baseline target disagrees
        if adj > 0:
            day_close_target[t] = max(day_close_target[t], cur * (1.0 + abs(adj) * floor_strength))
        elif adj < 0:
            day_close_target[t] = min(day_close_target[t], cur * (1.0 - abs(adj) * floor_strength))


def _update_candle(ticker: str, price: float):
    now = int(time.time())
    candles = ohlc_history[ticker]
    last = candles[-1] if candles else None
    if not last:
        candles.append({"ts": now, "o": price, "h": price, "l": price, "c": price})
        return

    if (now - last["ts"]) >= CANDLE_SECONDS:
        candles.append({"ts": now, "o": price, "h": price, "l": price, "c": price})
    else:
        last["h"] = max(last["h"], price)
        last["l"] = min(last["l"], price)
        last["c"] = price


def market_tick():
    global global_tick, event_running, status
    global market_ar_state, sector_ar_state, idio_ar_state

    with state_lock:
        if not event_running:
            _idle_tick()
            return

        if global_tick >= total_ticks:
            event_running = False
            status = "IDLE"
            return

        day_index = _current_day_index()
        if day_index != day_no:
            _start_new_day(day_index)

        # trigger scheduled news
        for sn in scheduled_news:
            if sn.tick == global_tick:
                apply_scheduled_news(sn)

        # end reaction
        if status == "REACTION" and global_tick > reaction_end_tick:
            status = "IDLE"
            for t in TICKERS:
                reaction_drift_add[t] = 0.0
                reaction_vol_mult[t] = 1.0

        # Smooth AR update
        phi = max(0.0, min(0.97, NOISE_AR_PHI))
        eps_scale = math.sqrt(max(0.0, 1.0 - phi * phi))

        market_ar_state = phi * market_ar_state + eps_scale * market_z[global_tick]
        for s in SECTORS:
            sector_ar_state[s] = phi * sector_ar_state[s] + eps_scale * sector_z[s][global_tick]

        pos = _day_pos()
        vol_curve = _intraday_vol_curve(pos)

        remaining_ticks = max(1, current_day_end_tick - global_tick + 1)
        late_pull = 0.15 + 0.85 * (pos ** 2.2)

        for t in TICKERS:
            p0 = float(prices[t])
            sec = TICKER_TO_COMPANY[t].get("sector", "Other")

            idio_ar_state[t] = phi * idio_ar_state[t] + eps_scale * idio_z[t][global_tick]

            base_vol = BASE_INTRADAY_VOL * vol_curve
            vol_mult = max(1.0, day_vol_mult.get(t, 1.0), reaction_vol_mult.get(t, 1.0))

            noise = base_vol * vol_mult * (
                market_beta[t] * market_ar_state
                + sector_beta[t] * sector_ar_state.get(sec, 0.0)
                + IDIO_WEIGHT * idio_ar_state[t]
            )

            # Pull to the (news-adjusted) close target
            target = float(day_close_target.get(t, p0))
            dist = math.log(max(0.01, target) / max(0.01, p0))
            pull = late_pull * (dist / remaining_ticks)

            drift = day_drift_add[t] + reaction_drift_add[t]
            ret = pull + drift + noise

            # clamp single-tick extremes
            ret = max(-0.045, min(0.045, ret))

            p1 = max(0.01, p0 * (1.0 + ret))

            prev_prices[t] = p0
            prices[t] = p1
            price_history[t].append(p1)
            _update_candle(t, p1)

        global_tick += 1


def _idle_tick():
    for t in TICKERS:
        p0 = float(prices[t])
        ret = 0.00002 * math.sin(time.time() / 9.0) + 0.00010 * math.sin(time.time() / 3.0)
        p1 = max(0.01, p0 * (1.0 + ret))
        prev_prices[t] = p0
        prices[t] = p1
        price_history[t].append(p1)
        _update_candle(t, p1)


def _require_admin(payload: Dict) -> bool:
    pw = (payload.get("password") or "").strip()
    return pw == ADMIN_PASSWORD


def _seconds_left_in_day() -> int:
    if not event_running:
        return 0
    remaining = max(0, (current_day_end_tick - global_tick + 1) * TICK_SECONDS)
    return int(remaining)


def _seconds_left_in_event() -> int:
    if not event_running:
        return 0
    remaining = max(0, (total_ticks - global_tick) * TICK_SECONDS)
    return int(remaining)


def _next_scheduled_news_eta() -> Optional[int]:
    if not event_running:
        return None
    for sn in scheduled_news:
        if sn.tick >= global_tick:
            return int((sn.tick - global_tick) * TICK_SECONDS)
    return None


def public_news_payload(n: Optional[Dict]) -> Optional[Dict]:
    if not n:
        return None
    return {
        "id": n.get("id"),
        "headline": n.get("headline"),
        "summary": n.get("summary"),
        "body": n.get("body"),
        "bullets": n.get("bullets", []),
        "sectors": n.get("sectors", []),
        "tickers": n.get("tickers", []),
    }


@app.get("/api/state")
@app.get("/api/latest_state")
def api_state():
    player = (request.args.get("player") or "").strip()
    with state_lock:
        q = quotes_for_all()
        lookback_ticks = max(1, int(MOVE_LOOKBACK_SECONDS / max(0.001, TICK_SECONDS)))

        price_rows = []
        for t in TICKERS:
            p = float(prices[t])
            hist = price_history[t]
            ref = float(hist[-1 - lookback_ticks]) if len(hist) > lookback_ticks else float(prev_prices.get(t, p))
            chg = (p - ref)
            chg_pct = (chg / ref * 100.0) if ref > 0 else 0.0

            price_rows.append({
                "ticker": t,
                "name": TICKER_TO_COMPANY[t]["name"],
                "sector": TICKER_TO_COMPANY[t].get("sector", "Other"),
                "price": round(p, 4),
                "change": round(chg, 4),
                "change_pct": round(chg_pct, 3),
                "bid": round(q[t]["bid"], 4),
                "ask": round(q[t]["ask"], 4),
                "spread_bps": round(q[t]["spread_bps"], 2),
                "spark": list(hist)[-30:],
                "ohlc": list(ohlc_history[t])[-60:],
                "impact": current_impact_map.get(t, "NONE") if current_news_internal else "NONE",
            })

        resp = {
            "event_running": event_running,
            "seed": event_seed if event_running else None,
            "day_no": day_no if event_running else 0,
            "total_days": total_days if event_running else 0,
            "seconds_left_in_day": _seconds_left_in_day(),
            "seconds_left_in_event": _seconds_left_in_event(),
            "next_news_eta": _next_scheduled_news_eta(),
            "status": status,
            "reaction_seconds_left": int(max(0, (reaction_end_tick - global_tick) * TICK_SECONDS)) if status == "REACTION" else 0,
            "current_news": public_news_payload(current_news_internal),
            "companies": COMPANIES,
            "sectors": SECTORS,
            "prices": price_rows,
            "leaderboard": compute_leaderboard(),
            "move_window_seconds": int(lookback_ticks * TICK_SECONDS),
        }

        if player:
            ensure_player(player)
            resp["portfolio"] = portfolio(player)

        return jsonify(resp)


@app.post("/api/trade")
def api_trade():
    payload = request.get_json(force=True, silent=True) or {}
    player = (payload.get("player") or "").strip()
    ticker = (payload.get("ticker") or "").strip().upper()
    side = (payload.get("side") or "").strip().upper()
    qty = int(payload.get("qty") or 0)

    if not player:
        return jsonify({"ok": False, "error": "Missing player"}), 400
    if ticker not in TICKER_TO_COMPANY:
        return jsonify({"ok": False, "error": "Invalid ticker"}), 400
    if side not in ("BUY", "SELL"):
        return jsonify({"ok": False, "error": "Invalid side"}), 400
    if qty <= 0 or qty > 1_000_000:
        return jsonify({"ok": False, "error": "Invalid quantity"}), 400

    with state_lock:
        ensure_player(player)
        q = quotes_for_all()[ticker]
        bid = q["bid"]
        ask = q["ask"]

        liq = float(liquidity_units.get(ticker, 20000.0))
        size_ratio = min(2.5, qty / max(1.0, liq))
        slip_pct = 0.0003 + 0.0018 * size_ratio

        if side == "BUY":
            px = ask * (1.0 + slip_pct)
            cost = px * qty
            fee = cost * (FEE_BPS / 10000.0)
            total = cost + fee
            if players[player]["cash"] < total:
                return jsonify({"ok": False, "error": "Insufficient cash"}), 400

            players[player]["cash"] -= total
            h = players[player]["holdings"].get(ticker)
            if not h:
                players[player]["holdings"][ticker] = {"qty": qty, "avg": px}
            else:
                old_qty = int(h["qty"])
                old_avg = float(h["avg"])
                new_qty = old_qty + qty
                new_avg = (old_avg * old_qty + px * qty) / new_qty
                h["qty"] = new_qty
                h["avg"] = new_avg

            players[player]["trades"].append({"ts": int(time.time()), "ticker": ticker, "side": "BUY", "qty": qty, "price": round(px, 4), "fee": round(fee, 4)})

        else:
            h = players[player]["holdings"].get(ticker)
            if not h or int(h["qty"]) < qty:
                return jsonify({"ok": False, "error": "Not enough shares"}), 400

            px = bid * (1.0 - slip_pct)
            proceeds = px * qty
            fee = proceeds * (FEE_BPS / 10000.0)
            net = proceeds - fee

            players[player]["cash"] += net
            h["qty"] = int(h["qty"]) - qty
            if int(h["qty"]) <= 0:
                players[player]["holdings"].pop(ticker, None)

            players[player]["trades"].append({"ts": int(time.time()), "ticker": ticker, "side": "SELL", "qty": qty, "price": round(px, 4), "fee": round(fee, 4)})

        return jsonify({"ok": True, "portfolio": portfolio(player)})


@app.post("/api/admin/login")
def api_admin_login():
    payload = request.get_json(force=True, silent=True) or {}
    return jsonify({"ok": _require_admin(payload)})


@app.post("/api/admin/start_event")
def api_admin_start_event():
    payload = request.get_json(force=True, silent=True) or {}
    if not _require_admin(payload):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    global event_running, event_seed, event_start_ts, event_end_ts
    global global_tick, day_no, status
    global market_ar_state, sector_ar_state, idio_ar_state

    with state_lock:
        event_seed = int(payload.get("seed") or getattr(config, "EVENT_SEED", 0) or 0)
        if event_seed <= 0:
            event_seed = int(time.time())

        build_event_plan(event_seed)

        for c in COMPANIES:
            t = c["ticker"]
            prices[t] = float(c["start_price"])
            prev_prices[t] = float(c["start_price"])
            price_history[t].clear()
            price_history[t].extend([prices[t]] * min(60, HIST_MAXLEN))
            ohlc_history[t].clear()
            ohlc_history[t].append({"ts": int(time.time()), "o": prices[t], "h": prices[t], "l": prices[t], "c": prices[t]})

        if bool(payload.get("reset_players", True)):
            players.clear()

        market_ar_state = 0.0
        for s in SECTORS:
            sector_ar_state[s] = 0.0
        for t in TICKERS:
            idio_ar_state[t] = 0.0

        event_running = True
        event_start_ts = time.time()
        event_end_ts = event_start_ts + EVENT_TOTAL_MINUTES * 60

        global_tick = 0
        day_no = 0
        status = "IDLE"

        return jsonify({"ok": True, "seed": event_seed, "total_days": total_days})


@app.post("/api/admin/stop_event")
def api_admin_stop_event():
    payload = request.get_json(force=True, silent=True) or {}
    if not _require_admin(payload):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    global event_running
    with state_lock:
        event_running = False
        return jsonify({"ok": True})


@app.get("/")
def index():
    return render_template("index.html", companies=COMPANIES, sectors=SECTORS)


@app.get("/game")
def game():
    player = (request.args.get("player") or "").strip()
    if not player:
        return redirect("/")
    return render_template("game.html", player_name=player, companies=COMPANIES, sectors=SECTORS)


@app.get("/presenter")
def presenter():
    return render_template("presenter.html", companies=COMPANIES, sectors=SECTORS)


@app.get("/admin")
def admin():
    return render_template("admin.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(getattr(config, "PORT", 5000)), debug=False)