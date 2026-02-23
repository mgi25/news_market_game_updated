# app.py — News Market Game (realism + difficulty upgrade)
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
def _cfg(name: str, default):
    return getattr(config, name, default)

def _cfg_float(name: str, default: float) -> float:
    try:
        return float(_cfg(name, default))
    except Exception:
        return float(default)

def _cfg_int(name: str, default: int) -> int:
    try:
        return int(_cfg(name, default))
    except Exception:
        return int(default)

def _cfg_str(name: str, default: str) -> str:
    try:
        return str(_cfg(name, default))
    except Exception:
        return str(default)

TICK_SECONDS = _cfg_float("TICK_SECONDS", 1.0)

EVENT_TOTAL_MINUTES = _cfg_int("EVENT_TOTAL_MINUTES", 90)
DAY_SECONDS_DEFAULT = _cfg_int("DAY_SECONDS", 150)
DAY_SECONDS_MIN = _cfg_int("DAY_SECONDS_MIN", 0)
DAY_SECONDS_MAX = _cfg_int("DAY_SECONDS_MAX", 0)

P_NO_NEWS = _cfg_float("P_NO_NEWS", 0.08)
P_TWO_NEWS = _cfg_float("P_TWO_NEWS", 0.25)
P_THREE_NEWS = _cfg_float("P_THREE_NEWS", 0.05)
MAX_NEWS_PER_DAY = _cfg_int("MAX_NEWS_PER_DAY", 3)

REACTION_SECONDS = _cfg_int("REACTION_SECONDS", 18)

START_CASH = _cfg_float("START_CASH", 100000.0)
FEE_BPS = _cfg_float("FEE_BPS", 2.0)

# Spread + microstructure
BASE_SPREAD_BPS = _cfg_float("BASE_SPREAD_BPS", 8.0)
VOL_SPREAD_BPS = _cfg_float("VOL_SPREAD_BPS", 35.0)

# Base vol + correlation
BASE_INTRADAY_VOL = _cfg_float("BASE_INTRADAY_VOL", 0.0010)
MARKET_BETA_RANGE = _cfg("MARKET_BETA_RANGE", (0.6, 1.2))
SECTOR_BETA_RANGE = _cfg("SECTOR_BETA_RANGE", (0.3, 0.9))
IDIO_WEIGHT = _cfg_float("IDIO_WEIGHT", 0.6)

# Smoother returns
NOISE_AR_PHI = _cfg_float("NOISE_AR_PHI", 0.88)

# UI move is over a window (less jumpy than last tick)
MOVE_LOOKBACK_SECONDS = _cfg_int("MOVE_LOOKBACK_SECONDS", 12)

NEWS_IMPACT_PCT = _cfg(
    "NEWS_IMPACT_PCT",
    {
        "LOW": (0.006, 0.012),
        "MEDIUM": (0.012, 0.025),
        "HIGH": (0.025, 0.055),
    },
)

DIRECT_WEIGHT = _cfg_float("DIRECT_WEIGHT", 1.00)
SECTOR_WEIGHT = _cfg_float("SECTOR_WEIGHT", 0.55)
LINKED_WEIGHT = _cfg_float("LINKED_WEIGHT", 0.25)

SECTOR_LINKS = _cfg("SECTOR_LINKS", {})

ADMIN_PASSWORD = _cfg_str("ADMIN_PASSWORD", "admin").strip()
CANDLE_SECONDS = _cfg_int("CANDLE_SECONDS", 5)

# -------------------------
# Realism + difficulty knobs
# -------------------------
DIFFICULTY = _cfg_str("DIFFICULTY", "MEDIUM").strip().upper()
_DIFF_PRESETS = {
    "EASY":   {"HOLD_BPS": 4.0,  "FLOW_K": 0.0060, "MR_K": 0.018, "BULL": 0.42, "SIDE": 0.34, "BEAR": 0.24, "NEWS_REV": 0.10},
    "MEDIUM": {"HOLD_BPS": 7.0,  "FLOW_K": 0.0075, "MR_K": 0.024, "BULL": 0.36, "SIDE": 0.32, "BEAR": 0.32, "NEWS_REV": 0.13},
    "HARD":   {"HOLD_BPS": 10.0, "FLOW_K": 0.0085, "MR_K": 0.030, "BULL": 0.32, "SIDE": 0.30, "BEAR": 0.38, "NEWS_REV": 0.15},
}
_PRESET = _DIFF_PRESETS.get(DIFFICULTY, _DIFF_PRESETS["MEDIUM"])

# Volatility clustering (EWMA of abs returns)
VOL_EWMA_ALPHA = _cfg_float("VOL_EWMA_ALPHA", 0.08)
VOL_CLUSTER_K  = _cfg_float("VOL_CLUSTER_K", 1.25)

# Liquidity dynamics
LIQ_MIN_FRAC        = _cfg_float("LIQ_MIN_FRAC", 0.25)
LIQ_VOL_SENS        = _cfg_float("LIQ_VOL_SENS", 0.45)
LIQ_NEWS_SENS       = _cfg_float("LIQ_NEWS_SENS", 0.55)
LIQ_RECOVER_ALPHA   = _cfg_float("LIQ_RECOVER_ALPHA", 0.06)
LIQ_SPREAD_BPS      = _cfg_float("LIQ_SPREAD_BPS", 16.0)
CLUSTER_SPREAD_BPS  = _cfg_float("CLUSTER_SPREAD_BPS", 18.0)

# Slippage model
SLIPPAGE_BASE_PCT = _cfg_float("SLIPPAGE_BASE_PCT", 0.00025)
SLIPPAGE_SIZE_K   = _cfg_float("SLIPPAGE_SIZE_K", 0.0022)
SLIPPAGE_VOL_K    = _cfg_float("SLIPPAGE_VOL_K", 0.00070)
SLIPPAGE_SPREAD_K = _cfg_float("SLIPPAGE_SPREAD_K", 0.35)

# Order-flow impact (crowded trades move price temporarily)
FLOW_IMPACT_K     = _cfg_float("FLOW_IMPACT_K", float(_PRESET["FLOW_K"]))
FLOW_IMPACT_DECAY = _cfg_float("FLOW_IMPACT_DECAY", 0.55)

# News digestion: impulse + decay
NEWS_SHOCK_DECAY = _cfg_float("NEWS_SHOCK_DECAY", 0.93)
NEWS_DRIFT_DECAY = _cfg_float("NEWS_DRIFT_DECAY", 0.995)
NEWS_HEAT_DECAY  = _cfg_float("NEWS_HEAT_DECAY", 0.965)

NEWS_MODE_PROBS = _cfg(
    "NEWS_MODE_PROBS",
    {"NORMAL": 0.60, "WEAK": 0.25, "REVERSE": float(_PRESET["NEWS_REV"])},
)

# Holding fee (kills infinite buy-and-hold)
HOLDING_FEE_BPS_PER_DAY = _cfg_float("HOLDING_FEE_BPS_PER_DAY", float(_PRESET["HOLD_BPS"]))

# Market regimes
REGIME_PROBS = _cfg(
    "REGIME_PROBS",
    {"BULL": float(_PRESET["BULL"]), "SIDEWAYS": float(_PRESET["SIDE"]), "BEAR": float(_PRESET["BEAR"])},
)
REGIME_DURATION_DAYS_RANGE = _cfg("REGIME_DURATION_DAYS_RANGE", (2, 5))

# Mean reversion toward fair value
FAIR_TARGET_BLEND = _cfg_float("FAIR_TARGET_BLEND", 0.65)
FAIR_VALUE_DAY_VOL = _cfg_float("FAIR_VALUE_DAY_VOL", 0.0030)
MEAN_REVERT_K = _cfg_float("MEAN_REVERT_K", float(_PRESET["MR_K"]))
CLOSE_PULL_K  = _cfg_float("CLOSE_PULL_K", 0.90)

# =========================
# Shared State
# =========================
state_lock = threading.Lock()

prices: Dict[str, float] = {c["ticker"]: float(c["start_price"]) for c in COMPANIES}
prev_prices: Dict[str, float] = dict(prices)

# History long enough for rolling-window moves
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
        "c": prices[t],
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

# vol multipliers (reaction / day)
day_vol_mult: Dict[str, float] = {t: 1.0 for t in TICKERS}
reaction_vol_mult: Dict[str, float] = {t: 1.0 for t in TICKERS}

# --- Realism state ---
liq_now: Dict[str, float] = {t: 0.0 for t in TICKERS}
vol_ewma: Dict[str, float] = {t: BASE_INTRADAY_VOL for t in TICKERS}

news_heat: Dict[str, float] = {t: 0.0 for t in TICKERS}
news_shock: Dict[str, float] = {t: 0.0 for t in TICKERS}  # impulse term added to return
news_drift: Dict[str, float] = {t: 0.0 for t in TICKERS}  # slower decay term

pending_flow: Dict[str, float] = {t: 0.0 for t in TICKERS}
flow_impact_state: Dict[str, float] = {t: 0.0 for t in TICKERS}

fair_value: Dict[str, float] = {t: float(prices[t]) for t in TICKERS}

market_regime: str = "SIDEWAYS"
regime_days_left: int = 0
regime_vol_mult: float = 1.0
regime_close_pull_mult: float = 1.0
regime_mean_revert_mult: float = 1.0
regime_bias: float = 0.0  # daily drift bias via close-target generation

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
            # keep server alive even if a tick throws
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

def _vfac(ticker: str) -> float:
    v = float(vol_ewma.get(ticker, BASE_INTRADAY_VOL))
    base = max(1e-9, BASE_INTRADAY_VOL)
    return max(0.7, min(4.0, v / base))

def _current_spread_bps(ticker: str) -> float:
    """
    Spread widens with:
    - reaction/day volatility multipliers
    - clustered volatility (EWMA)
    - low liquidity
    """
    bps = float(BASE_SPREAD_BPS)

    vm = max(1.0, day_vol_mult.get(ticker, 1.0), reaction_vol_mult.get(ticker, 1.0))
    bps += max(0.0, (vm - 1.0)) * float(VOL_SPREAD_BPS)

    vfac = _vfac(ticker)
    bps += max(0.0, vfac - 1.0) * float(CLUSTER_SPREAD_BPS)

    base_liq = float(liquidity_units.get(ticker, 20000.0))
    cur_liq = float(liq_now.get(ticker, base_liq))
    tight = max(1.0, min(4.0, base_liq / max(1.0, cur_liq)))
    bps += max(0.0, tight - 1.0) * float(LIQ_SPREAD_BPS)

    return max(1.0, min(300.0, bps))

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

def _weighted_choice(rng: random.Random, probs: Dict[str, float], order: List[str]) -> str:
    total = 0.0
    for k in order:
        total += float(probs.get(k, 0.0))
    if total <= 0:
        return order[0]
    r = rng.random() * total
    cum = 0.0
    for k in order:
        cum += float(probs.get(k, 0.0))
        if r <= cum:
            return k
    return order[-1]

def _roll_regime(rng: random.Random):
    """
    Sets global regime + multipliers.
    """
    global market_regime, regime_days_left, regime_vol_mult, regime_close_pull_mult, regime_mean_revert_mult, regime_bias

    mode = _weighted_choice(rng, REGIME_PROBS, ["BULL", "SIDEWAYS", "BEAR"])
    market_regime = mode

    lo, hi = REGIME_DURATION_DAYS_RANGE
    lo = int(lo) if isinstance(lo, (int, float)) else 2
    hi = int(hi) if isinstance(hi, (int, float)) else 5
    if hi < lo:
        hi = lo
    regime_days_left = rng.randint(max(1, lo), max(1, hi))

    if mode == "BULL":
        regime_bias = +0.0022
        regime_vol_mult = 0.95
        regime_close_pull_mult = 0.95
        regime_mean_revert_mult = 0.85
    elif mode == "BEAR":
        regime_bias = -0.0028
        regime_vol_mult = 1.20
        regime_close_pull_mult = 0.95
        regime_mean_revert_mult = 0.90
    else:  # SIDEWAYS
        regime_bias = 0.0
        regime_vol_mult = 1.00
        regime_close_pull_mult = 1.10
        regime_mean_revert_mult = 1.20

def _apply_holding_fee_for_day():
    """
    Charge holding fee once per day across all open positions (long only).
    Prevents "buy and hold forever" from dominating.
    """
    if HOLDING_FEE_BPS_PER_DAY <= 0:
        return

    rate = HOLDING_FEE_BPS_PER_DAY / 10000.0
    ts = int(time.time())

    for name, p in players.items():
        hv = 0.0
        for t, h in p.get("holdings", {}).items():
            hv += float(prices.get(t, 0.0)) * int(h.get("qty", 0))
        if hv <= 0:
            continue

        fee = hv * rate
        p["cash"] = float(p.get("cash", 0.0)) - fee
        p["trades"].append({
            "ts": ts,
            "ticker": "__HOLDING_FEE__",
            "side": "FEE",
            "qty": 0,
            "price": 0.0,
            "fee": round(fee, 4),
        })

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
        market_beta_local[t] = float(mb_lo) + (float(mb_hi) - float(mb_lo)) * rng.random()
        sector_beta_local[t] = float(sb_lo) + (float(sb_hi) - float(sb_lo)) * rng.random()
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
    global regime_days_left

    day_no = day_index
    _set_current_day_bounds(day_index)

    # Regime management (roll if needed)
    rng_reg = random.Random(event_seed + day_index * 777)
    if regime_days_left <= 0:
        _roll_regime(rng_reg)
    # consume one day of regime
    regime_days_left = max(0, regime_days_left - 1)

    # Reset per-day multipliers
    for t in TICKERS:
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

    # Daily themes
    rng = random.Random(event_seed + day_index * 999)

    def clamp(x, lo, hi):
        return max(lo, min(hi, x))

    # Regime affects the mean drift (bias)
    market_theme = rng.uniform(-0.004, 0.004) + float(regime_bias)
    sector_theme: Dict[str, float] = {s: rng.uniform(-0.003, 0.003) for s in SECTORS}

    for t in TICKERS:
        sec = TICKER_TO_COMPANY[t].get("sector", "Other")
        idio = rng.uniform(-0.004, 0.004)

        day_ret = (market_theme * market_beta[t]) + (sector_theme.get(sec, 0.0) * sector_beta[t]) + (0.6 * idio)
        # a bit wider range under bear regime
        spread = 0.014 if market_regime == "BEAR" else 0.012
        day_ret = clamp(day_ret, -spread, spread)

        base = day_open_price[t] * (1.0 + day_ret)
        base_day_close_target[t] = base
        day_close_target[t] = base

        # Update fair value (slow-moving anchor)
        # Blend old fair value with today's baseline target + a small daily random walk
        fv0 = float(fair_value.get(t, prices[t]))
        walk = rng.uniform(-FAIR_VALUE_DAY_VOL, FAIR_VALUE_DAY_VOL)
        fv1 = (FAIR_TARGET_BLEND * fv0) + ((1.0 - FAIR_TARGET_BLEND) * base)
        fv1 = fv1 * (1.0 + walk)
        fair_value[t] = max(0.01, fv1)

def apply_scheduled_news(sn: ScheduledNews):
    """
    News realism:
    - Impact uses impulse + decay (news_shock/news_drift)
    - News sometimes WEAK or REVERSE (priced-in / fade)
    - Still shifts close target so the headline direction is usually visible
    """
    global current_news_internal, current_impact_map, status, reaction_end_tick

    current_news_internal = sn.news
    current_impact_map = sn.impact_map
    status = "REACTION"

    reaction_ticks = max(3, int(REACTION_SECONDS / max(0.001, TICK_SECONDS)))
    reaction_end_tick = min(current_day_end_tick, global_tick + reaction_ticks)

    # Deterministic mode selection for fairness
    rng = random.Random(event_seed + sn.tick * 1337 + sn.day_index * 911)
    mode = _weighted_choice(rng, NEWS_MODE_PROBS, ["NORMAL", "WEAK", "REVERSE"])

    total = float(sn.impact_total_pct)
    if mode == "WEAK":
        total *= 0.55

    # Shock follows headline direction; drift may reverse in REVERSE mode
    drift_total = total
    if mode == "REVERSE":
        drift_total = -0.60 * total  # fade/reversal after initial reaction

    # Partition
    shock_amp  = 0.55 * total
    target_amp = 0.35 * total
    drift_amp  = 0.10 * drift_total

    # Convert to decayed impulses so the total effect is close to the amplitude
    shock_imp = shock_amp * (1.0 - NEWS_SHOCK_DECAY)
    drift_imp = drift_amp * (1.0 - NEWS_DRIFT_DECAY)

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

        # Impulse + decay (more realistic digestion)
        news_shock[t] += w * shock_imp
        news_drift[t] += w * drift_imp
        news_heat[t] = min(3.0, float(news_heat.get(t, 0.0)) + abs(w * total) * 1.8)

        # Shift end-of-day target (so headline direction generally matters)
        adj = w * target_amp
        day_target_mult[t] *= (1.0 + adj)
        base = float(base_day_close_target.get(t, prices[t]))
        day_close_target[t] = base * day_target_mult[t]

        # Ensure the direction shows (but less "forced" than before)
        cur = float(prices[t])
        floor_strength = 0.40
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

def _update_liquidity(ticker: str):
    base = float(liquidity_units.get(ticker, 20000.0))
    cur = float(liq_now.get(ticker, base))

    vfac = _vfac(ticker)
    heat = float(news_heat.get(ticker, 0.0))

    # target liquidity reduces under higher vol + news heat
    denom = 1.0 + (LIQ_VOL_SENS * max(0.0, vfac - 1.0)) + (LIQ_NEWS_SENS * heat)
    target = base / max(1e-6, denom)

    lo = base * LIQ_MIN_FRAC
    hi = base * 1.15
    target = max(lo, min(hi, target))

    # smooth recover toward target
    cur = (1.0 - LIQ_RECOVER_ALPHA) * cur + LIQ_RECOVER_ALPHA * target
    liq_now[ticker] = cur

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
            # holding fee at day boundary (not before day 1)
            if day_no > 0:
                _apply_holding_fee_for_day()
            _start_new_day(day_index)

        # trigger scheduled news at this tick
        # (loop is fine because schedule size is small)
        for sn in scheduled_news:
            if sn.tick == global_tick:
                apply_scheduled_news(sn)

        # end reaction window
        if status == "REACTION" and global_tick > reaction_end_tick:
            status = "IDLE"
            for t in TICKERS:
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
        late_pull = (0.15 + 0.85 * (pos ** 2.2)) * float(CLOSE_PULL_K) * float(regime_close_pull_mult)

        # Apply order-flow impact (compute state from pending_flow)
        for t in TICKERS:
            base_liq = float(liquidity_units.get(t, 20000.0))
            liq = float(liq_now.get(t, base_liq)) if liq_now.get(t, 0.0) > 0 else base_liq
            flow = float(pending_flow.get(t, 0.0))
            ratio = flow / max(1.0, liq)
            flow_impact_state[t] = (FLOW_IMPACT_DECAY * float(flow_impact_state.get(t, 0.0))) + ((1.0 - FLOW_IMPACT_DECAY) * ratio)
            pending_flow[t] = 0.0  # consume per tick

        for t in TICKERS:
            p0 = float(prices[t])
            sec = TICKER_TO_COMPANY[t].get("sector", "Other")

            # idio AR state
            idio_ar_state[t] = phi * idio_ar_state[t] + eps_scale * idio_z[t][global_tick]

            # Decay news states + heat
            news_shock[t] *= float(NEWS_SHOCK_DECAY)
            news_drift[t] *= float(NEWS_DRIFT_DECAY)
            news_heat[t] *= float(NEWS_HEAT_DECAY)

            # Liquidity update (depends on current vol/news heat)
            _update_liquidity(t)

            # Volatility clustering factor
            vfac = _vfac(t)
            cluster_mult = 1.0 + float(VOL_CLUSTER_K) * max(0.0, vfac - 1.0)
            cluster_mult = max(0.8, min(2.8, cluster_mult))

            # Base vol with intraday curve + regime + cluster + news multipliers
            base_vol = float(BASE_INTRADAY_VOL) * float(vol_curve)
            vol_mult = max(1.0, day_vol_mult.get(t, 1.0), reaction_vol_mult.get(t, 1.0))
            vol_mult *= float(regime_vol_mult) * float(cluster_mult)

            noise = base_vol * vol_mult * (
                market_beta[t] * market_ar_state
                + sector_beta[t] * sector_ar_state.get(sec, 0.0)
                + IDIO_WEIGHT * idio_ar_state[t]
            )

            # Pull to the (news-adjusted) close target
            target = float(day_close_target.get(t, p0))
            dist = math.log(max(0.01, target) / max(0.01, p0))
            pull = late_pull * (dist / remaining_ticks)

            # Mean reversion toward fair value (prevents permanent trending)
            fv = float(fair_value.get(t, p0))
            mr = float(MEAN_REVERT_K) * float(regime_mean_revert_mult) * ((fv - p0) / max(0.01, p0))

            # News digestion (impulse + drift) + order-flow impact
            news_term = float(news_shock.get(t, 0.0)) + float(news_drift.get(t, 0.0))
            flow_term = float(FLOW_IMPACT_K) * float(flow_impact_state.get(t, 0.0))

            ret = pull + mr + news_term + flow_term + noise

            # clamp single-tick extremes
            ret = max(-0.050, min(0.050, ret))

            p1 = max(0.01, p0 * (1.0 + ret))

            # update vol EWMA using realized abs return
            vol_ewma[t] = (1.0 - VOL_EWMA_ALPHA) * float(vol_ewma.get(t, BASE_INTRADAY_VOL)) + VOL_EWMA_ALPHA * abs(ret)
            vol_ewma[t] = max(1e-6, min(0.020, vol_ewma[t]))

            prev_prices[t] = p0
            prices[t] = p1
            price_history[t].append(p1)
            _update_candle(t, p1)

        global_tick += 1

def _idle_tick():
    # keep tiny motion when idle
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

# =========================
# API
# =========================
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
            "regime": market_regime if event_running else None,
            "difficulty": DIFFICULTY,
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
        bid = float(q["bid"])
        ask = float(q["ask"])
        spread_bps = float(q["spread_bps"])

        # Slippage depends on size vs liquidity, clustered vol, and spread
        liq_base = float(liquidity_units.get(ticker, 20000.0))
        liq = float(liq_now.get(ticker, liq_base)) if liq_now.get(ticker, 0.0) > 0 else liq_base
        size_ratio = min(3.0, qty / max(1.0, liq))

        vfac = _vfac(ticker)
        slip_pct = (
            SLIPPAGE_BASE_PCT
            + SLIPPAGE_SIZE_K * size_ratio
            + SLIPPAGE_VOL_K * max(0.0, vfac - 1.0)
            + SLIPPAGE_SPREAD_K * (spread_bps / 10000.0)
        )
        slip_pct = max(0.0, min(0.02, slip_pct))

        if side == "BUY":
            px = ask * (1.0 + slip_pct)
            cost = px * qty
            fee = cost * (FEE_BPS / 10000.0)
            total = cost + fee
            if float(players[player]["cash"]) < total:
                return jsonify({"ok": False, "error": "Insufficient cash"}), 400

            players[player]["cash"] = float(players[player]["cash"]) - total
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

            players[player]["trades"].append({
                "ts": int(time.time()),
                "ticker": ticker,
                "side": "BUY",
                "qty": qty,
                "price": round(px, 4),
                "fee": round(fee, 4),
            })

            # Order-flow impact (crowding)
            pending_flow[ticker] = float(pending_flow.get(ticker, 0.0)) + float(qty)

        else:
            h = players[player]["holdings"].get(ticker)
            if not h or int(h["qty"]) < qty:
                return jsonify({"ok": False, "error": "Not enough shares"}), 400

            px = bid * (1.0 - slip_pct)
            proceeds = px * qty
            fee = proceeds * (FEE_BPS / 10000.0)
            net = proceeds - fee

            players[player]["cash"] = float(players[player]["cash"]) + net
            h["qty"] = int(h["qty"]) - qty
            if int(h["qty"]) <= 0:
                players[player]["holdings"].pop(ticker, None)

            players[player]["trades"].append({
                "ts": int(time.time()),
                "ticker": ticker,
                "side": "SELL",
                "qty": qty,
                "price": round(px, 4),
                "fee": round(fee, 4),
            })

            pending_flow[ticker] = float(pending_flow.get(ticker, 0.0)) - float(qty)

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
    global market_regime, regime_days_left, regime_vol_mult, regime_close_pull_mult, regime_mean_revert_mult, regime_bias

    with state_lock:
        event_seed = int(payload.get("seed") or _cfg_int("EVENT_SEED", 0) or 0)
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

        # Reset realism state
        for t in TICKERS:
            vol_ewma[t] = float(BASE_INTRADAY_VOL)
            news_heat[t] = 0.0
            news_shock[t] = 0.0
            news_drift[t] = 0.0
            pending_flow[t] = 0.0
            flow_impact_state[t] = 0.0
            fair_value[t] = float(prices[t])
            liq_now[t] = float(liquidity_units.get(t, 20000.0))

        # Reset regimes
        market_regime = "SIDEWAYS"
        regime_days_left = 0
        regime_vol_mult = 1.0
        regime_close_pull_mult = 1.0
        regime_mean_revert_mult = 1.0
        regime_bias = 0.0

        event_running = True
        event_start_ts = time.time()
        event_end_ts = event_start_ts + EVENT_TOTAL_MINUTES * 60

        global_tick = 0
        day_no = 0
        status = "IDLE"

        return jsonify({"ok": True, "seed": event_seed, "total_days": total_days, "difficulty": DIFFICULTY})

@app.post("/api/admin/stop_event")
def api_admin_stop_event():
    payload = request.get_json(force=True, silent=True) or {}
    if not _require_admin(payload):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    global event_running
    with state_lock:
        event_running = False
        return jsonify({"ok": True})

# =========================
# Pages
# =========================
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
    app.run(host="0.0.0.0", port=int(_cfg_int("PORT", 5000)), debug=False)