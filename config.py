# =========================
# EVENT SETTINGS (Auto-run)
# =========================
EVENT_TOTAL_MINUTES = 90          # total event duration ~1.5 hours

# Day length: choose ONE approach:
DAY_SECONDS = 150                 # fixed day length (2.5 minutes)

# OR if you want varying day length between 2 and 3 minutes, use these and keep DAY_SECONDS present:
DAY_SECONDS_MIN = 120
DAY_SECONDS_MAX = 180

# News frequency per day
P_NO_NEWS = 0.08                  # rare: no news day
P_TWO_NEWS = 0.25                 # sometimes: 2 news in a day
P_THREE_NEWS = 0.05               # rare: 3 news in a day
MAX_NEWS_PER_DAY = 3

# Reaction spike duration (visual high-vol window)
REACTION_SECONDS = 18


# =========================
# MARKET REALISM SETTINGS
# =========================
# Higher = more movement (tweak if needed)
BASE_INTRADAY_VOL = 0.0012

# U-shaped open/close behavior is in app.py; this controls base volatility
MARKET_BETA_RANGE = (0.6, 1.2)
SECTOR_BETA_RANGE = (0.3, 0.9)
IDIO_WEIGHT = 0.6

# News total impact % ranges (total daily effect contribution)
NEWS_IMPACT_PCT = {
    "LOW": (0.006, 0.012),        # 0.6% - 1.2%
    "MEDIUM": (0.012, 0.025),     # 1.2% - 2.5%
    "HIGH": (0.025, 0.055),       # 2.5% - 5.5%
}

# News spillover weights
DIRECT_WEIGHT = 1.00
SECTOR_WEIGHT = 0.55
LINKED_WEIGHT = 0.25

# Optional: sector spillovers (mild correlation)
SECTOR_LINKS = {
    "Tech": ["Banking", "Consumer"],
    "Banking": ["Tech", "Industrials"],
    "Energy": ["Industrials", "Consumer"],
    "Healthcare": ["Consumer"],
    "Consumer": ["Tech", "Industrials"],
    "Industrials": ["Energy", "Banking"],
}

# =========================
# Trading microstructure
# =========================
START_CASH = 100000.0
FEE_BPS = 2.0
BASE_SPREAD_BPS = 8.0
VOL_SPREAD_BPS = 35.0

# Candle size for UI charting
CANDLE_SECONDS = 5