# config.py — News Market Game configuration
# Tuned for: beginner-friendly gameplay, visible news impact, ~1.5–2 hour event

# -------------------- App / Server --------------------
HOST = "0.0.0.0"
PORT = 5000  # Render/Gunicorn can override this in deployment if needed

# -------------------- Game Basics --------------------
START_CASH = 100000          # virtual cash per player
TICK_SECONDS = 1.0           # market updates every 1 second
REACTION_SECONDS = 40        # how long each news reaction window lasts
CANDLE_SECONDS = 10          # OHLC candle duration for charting (if UI uses it)

# -------------------- Admin --------------------
ADMIN_PASSWORD = "admin123"  # change this before hosting publicly

# -------------------- News Impact Weights --------------------
# DIRECT = specifically named company / directly targeted ticker
# SECTOR = same sector spillover
# LINKED = related sector spillover
DIRECT_WEIGHT = 1.00
SECTOR_WEIGHT = 0.55
LINKED_WEIGHT = 0.22

# Sector relationships (spillover)
SECTOR_LINKS = {
    "Tech":        ["Telecom", "Consumer"],
    "Banking":     ["RealEstate", "Consumer"],
    "Telecom":     ["Tech", "Consumer"],
    "Consumer":    ["Tech", "Banking", "RealEstate"],
    "Healthcare":  ["Consumer"],
    "Energy":      ["Industrials", "Consumer", "Telecom"],
    "Industrials": ["Energy", "Banking", "RealEstate"],
    "RealEstate":  ["Banking", "Industrials", "Consumer"],
}

# Optional inverse impact on UP news (cost pressure / rate pressure style)
# Example: if Energy gets strong UP news, some cost-sensitive sectors may feel pressure.
SECTOR_INVERSE = {
    "Energy": ["Consumer", "Industrials", "Telecom"],
    "Banking": ["RealEstate"],  # e.g., strong banking/rate sentiment can pressure property names
}

# -------------------- Market Microstructure (Realistic Feel) --------------------
# These affect execution price realism (spread/slippage/fee) without making it too harsh.
BASE_SPREAD_PCT = 0.0010     # 0.10% base spread
SPREAD_VOL_K = 4.8           # spread widens when volatility increases

BASE_SLIP_PCT = 0.00018      # 0.018% base slippage
SLIP_QTY_K = 0.020           # qty impact on slippage (higher = more penalty for big orders)
SLIP_VOL_K = 0.70            # volatility impact on slippage

TRADE_FEE_PCT = 0.0005       # 0.05% fee per trade
MIN_FEE = 1.0                # minimum flat fee in virtual currency

# -------------------- Market Dynamics (Per-Sector Personality) --------------------
# Base volatility per tick (log-return scale-ish). Higher = more movement.
# Keep these modest because news shocks add extra volatility.
BASE_VOL_BY_SECTOR = {
    "Tech":        0.00120,
    "Banking":     0.00095,
    "Telecom":     0.00085,
    "Consumer":    0.00090,
    "Healthcare":  0.00100,
    "Energy":      0.00135,
    "Industrials": 0.00105,
    "RealEstate":  0.00110,
}

# Higher liquidity = lower slippage for same order size
LIQUIDITY_BY_SECTOR = {
    "Tech":        12000,
    "Banking":     15000,
    "Telecom":     13000,
    "Consumer":    11000,
    "Healthcare":  9000,
    "Energy":      10000,
    "Industrials": 8500,
    "RealEstate":  7000,
}

# Core dynamics controls (how prices evolve tick-to-tick)
MIN_VOL = 0.00055       # floor volatility
VOL_SMOOTH = 0.92       # higher = smoother volatility
SHOCK_DECAY = 0.90      # news volatility fades each tick
TREND_DECAY = 0.93      # news drift fades each tick
MEAN_REVERT_K = 0.055   # pull prices back toward fair value over time
FAIR_SMOOTH = 0.995     # fair value slowly follows market

# -------------------- News Intensity (fallback / compatibility) --------------------
# Used as fallback if NEWS_PROFILE is not referenced or for future logic.
INTENSITY_RANGES = {
    "LOW":    (0.012, 0.025),  # 1.2% to 2.5% total intended direct move
    "MEDIUM": (0.025, 0.050),  # 2.5% to 5.0%
    "HIGH":   (0.050, 0.090),  # 5.0% to 9.0%
}

# -------------------- News Reaction Profiles (recommended) --------------------
# This directly powers the new app.py:
# - jump_pct: immediate gap move when news triggers
# - trend_per_tick: sustained directional drift during reaction window
# - vol_boost: extra volatility/choppiness during reaction
#
# Values are tuned so impacted names move MUCH more than normal background noise.
NEWS_PROFILE = {
    "LOW": {
        "jump_pct":       (0.006, 0.014),    # 0.6% - 1.4% instant move
        "trend_per_tick": (0.00010, 0.00030),
        "vol_boost":      (0.00025, 0.00070),
    },
    "MEDIUM": {
        "jump_pct":       (0.014, 0.032),    # 1.4% - 3.2% instant move
        "trend_per_tick": (0.00022, 0.00065),
        "vol_boost":      (0.00060, 0.00160),
    },
    "HIGH": {
        "jump_pct":       (0.030, 0.070),    # 3.0% - 7.0% instant move
        "trend_per_tick": (0.00040, 0.00120),
        "vol_boost":      (0.00120, 0.00320),
    },
}

# -------------------- Optional (Future Use) Round Pacing --------------------
# Your current app.py doesn't require these yet, but keeping them here is useful
# if you later re-enable structured round pacing and card effects.
PHASE_RULES = [
    {"start": 1,  "end": 8,  "label": "WARMUP", "duration_mult": 0.90, "jump_mult": 0.90, "trend_mult": 0.95, "vol_mult": 0.95, "min_move_mult": 0.90},
    {"start": 9,  "end": 22, "label": "MAIN",   "duration_mult": 1.00, "jump_mult": 1.00, "trend_mult": 1.00, "vol_mult": 1.00, "min_move_mult": 1.00},
    {"start": 23, "end": 28, "label": "HEAT",   "duration_mult": 1.05, "jump_mult": 1.10, "trend_mult": 1.08, "vol_mult": 1.12, "min_move_mult": 1.12},
    {"start": 29, "end": 30, "label": "FINALE", "duration_mult": 1.10, "jump_mult": 1.18, "trend_mult": 1.14, "vol_mult": 1.20, "min_move_mult": 1.20},
]

CARD_EFFECTS = {
    "NUMBER": {"duration_mult": 1.00, "jump_mult": 1.00, "trend_mult": 1.00, "vol_mult": 1.00, "min_move_mult": 1.00},
    "J":      {"duration_mult": 1.02, "jump_mult": 1.08, "trend_mult": 1.06, "vol_mult": 1.08, "min_move_mult": 1.08},
    "Q":      {"duration_mult": 1.03, "jump_mult": 1.10, "trend_mult": 1.08, "vol_mult": 1.10, "min_move_mult": 1.10},
    "K":      {"duration_mult": 1.05, "jump_mult": 1.14, "trend_mult": 1.10, "vol_mult": 1.14, "min_move_mult": 1.14},
    "A":      {"duration_mult": 1.08, "jump_mult": 1.22, "trend_mult": 1.16, "vol_mult": 1.22, "min_move_mult": 1.22},
    "JOKER":  {"duration_mult": 1.12, "jump_mult": 1.32, "trend_mult": 1.24, "vol_mult": 1.32, "min_move_mult": 1.32},
}