# =========================
# EVENT SETTINGS (Auto-run)
# =========================
EVENT_TOTAL_MINUTES = 90          # total event duration ~1.5 hours

# Server
PORT = 5000

# Day length: choose ONE approach:
DAY_SECONDS = 150                 # fixed day length (2.5 minutes)

# OR if you want varying day length between 2 and 3 minutes:
DAY_SECONDS_MIN = 120
DAY_SECONDS_MAX = 180

# News frequency per day
P_NO_NEWS = 0.08
P_TWO_NEWS = 0.25
P_THREE_NEWS = 0.05
MAX_NEWS_PER_DAY = 3

# Reaction spike duration (visual high-vol window)
REACTION_SECONDS = 18


# =========================
# MARKET REALISM + DIFFICULTY
# =========================
# Difficulty preset: EASY / MEDIUM / HARD
DIFFICULTY = "HARD"

BASE_INTRADAY_VOL = 0.0012

MARKET_BETA_RANGE = (0.6, 1.2)
SECTOR_BETA_RANGE = (0.3, 0.9)
IDIO_WEIGHT = 0.6

# Smoother correlated noise (AR(1))
NOISE_AR_PHI = 0.88

NEWS_IMPACT_PCT = {
    "LOW": (0.006, 0.012),
    "MEDIUM": (0.012, 0.025),
    "HIGH": (0.025, 0.055),
}

DIRECT_WEIGHT = 1.00
SECTOR_WEIGHT = 0.55
LINKED_WEIGHT = 0.25

SECTOR_LINKS = {
    "Tech": ["Banking", "Consumer"],
    "Banking": ["Tech", "Industrials"],
    "Energy": ["Industrials", "Consumer"],
    "Healthcare": ["Consumer"],
    "Consumer": ["Tech", "Industrials"],
    "Industrials": ["Energy", "Banking"],
}

# News doesn't always work (priced-in / weak / reversal)
NEWS_MODE_PROBS = {
    "NORMAL": 0.60,
    "WEAK": 0.25,
    "REVERSE": 0.15,
}


# =========================
# Trading microstructure
# =========================
START_CASH = 100000.0
FEE_BPS = 2.0
BASE_SPREAD_BPS = 8.0
VOL_SPREAD_BPS = 35.0

# Volatility clustering (EWMA of abs returns)
VOL_EWMA_ALPHA = 0.08
VOL_CLUSTER_K = 1.25

# Liquidity dynamics
LIQ_MIN_FRAC = 0.25
LIQ_VOL_SENS = 0.45
LIQ_NEWS_SENS = 0.55
LIQ_RECOVER_ALPHA = 0.06
LIQ_SPREAD_BPS = 16.0
CLUSTER_SPREAD_BPS = 18.0

# Slippage model
SLIPPAGE_BASE_PCT = 0.00025
SLIPPAGE_SIZE_K = 0.0022
SLIPPAGE_VOL_K = 0.00070
SLIPPAGE_SPREAD_K = 0.35

# Order-flow impact (crowded trades move price temporarily)
FLOW_IMPACT_K = 0.0085
FLOW_IMPACT_DECAY = 0.55

# News digestion: impulse + decay
NEWS_SHOCK_DECAY = 0.93
NEWS_DRIFT_DECAY = 0.995
NEWS_HEAT_DECAY = 0.965

# Holding fee (kills infinite buy-and-hold)
HOLDING_FEE_BPS_PER_DAY = 10.0

# Market regimes
REGIME_PROBS = {"BULL": 0.32, "SIDEWAYS": 0.30, "BEAR": 0.38}
REGIME_DURATION_DAYS_RANGE = (2, 5)

# Mean reversion toward fair value
FAIR_TARGET_BLEND = 0.65
FAIR_VALUE_DAY_VOL = 0.0030
MEAN_REVERT_K = 0.030
CLOSE_PULL_K = 0.90


# =========================
# UI / misc
# =========================
CANDLE_SECONDS = 5
MOVE_LOOKBACK_SECONDS = 12

ADMIN_PASSWORD = "admin"