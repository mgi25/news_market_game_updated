import os

HOST = os.getenv("NMG_HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", os.getenv("NMG_PORT", "8000")))

ADMIN_PASSWORD = os.getenv("NMG_ADMIN_PASSWORD", "admin123")

# Gameplay
START_CASH = float(os.getenv("NMG_START_CASH", "100000"))
TICK_SECONDS = float(os.getenv("NMG_TICK_SECONDS", "1.0"))

# News reaction window
REACTION_SECONDS = int(os.getenv("NMG_REACTION_SECONDS", "45"))

# ---------------- Realistic Market Engine ----------------

# Baseline volatility per tick (small, calm movement)
BASE_VOL_BY_SECTOR = {
    "Tech": 0.0012,
    "Telecom": 0.0010,
    "Banking": 0.00085,
    "Energy": 0.0011,
    "Healthcare": 0.00095,
    "Consumer": 0.0010,
    "Industrials": 0.0010,
    "RealEstate": 0.00095,
}

# Volatility clustering
MIN_VOL = 0.00025
VOL_SMOOTH = 0.97          # higher = smoother vol regime
SHOCK_DECAY = 0.94         # news shock lasts longer

# Trend + mean reversion
TREND_DECAY = 0.965
MEAN_REVERT_K = 0.06
FAIR_SMOOTH = 0.995

# ---------------- Execution realism ----------------

# Spread model
BASE_SPREAD_PCT = 0.0009   # 0.09% base
SPREAD_VOL_K = 18.0        # spread widens with volatility

# Slippage model
BASE_SLIP_PCT = 0.0002     # 0.02% baseline
SLIP_QTY_K = 0.000015      # qty/liquidity impact
SLIP_VOL_K = 6.0           # more slip during volatility

# Liquidity (higher = less slippage)
LIQUIDITY_BY_SECTOR = {
    "Tech": 6500,
    "Telecom": 8000,
    "Banking": 12000,
    "Energy": 9500,
    "Healthcare": 9000,
    "Consumer": 8500,
    "Industrials": 9000,
    "RealEstate": 8000,
}

# ---------------- News spillover ----------------

DIRECT_WEIGHT = 1.00
SECTOR_WEIGHT = 0.35
LINKED_WEIGHT = 0.18

SECTOR_LINKS = {
    "Tech":        ["Telecom", "Industrials"],
    "Telecom":     ["Tech"],
    "Banking":     ["Consumer", "RealEstate"],
    "Energy":      ["Industrials", "Consumer"],
    "Healthcare":  ["Consumer"],
    "Consumer":    ["Banking", "Industrials"],
    "Industrials": ["Energy", "Consumer"],
    "RealEstate":  ["Banking"],
}

# Opposite reactions (simple cost-shock model)
# Example: Energy UP can pressure Consumer slightly
SECTOR_INVERSE = {
    "Energy": ["Consumer"],
}

# ---------------- News impact profile (BIG moves) ----------------
# jump_pct is immediate jump after news.
# trend_per_tick continues the push during the reaction window.
# vol_boost spikes volatility during reaction.
NEWS_PROFILE = {
    "LOW": {
        "jump_pct": (0.006, 0.012),        # 0.6% to 1.2%
        "trend_per_tick": (0.00008, 0.00016),
        "vol_boost": (0.0010, 0.0020),
    },
    "MEDIUM": {
        "jump_pct": (0.012, 0.025),        # 1.2% to 2.5%
        "trend_per_tick": (0.00016, 0.00030),
        "vol_boost": (0.0020, 0.0040),
    },
    "HIGH": {
        "jump_pct": (0.025, 0.055),        # 2.5% to 5.5%
        "trend_per_tick": (0.00030, 0.00055),
        "vol_boost": (0.0040, 0.0070),
    },
}