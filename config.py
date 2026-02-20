import os

HOST = os.getenv("NMG_HOST", "0.0.0.0")
PORT = int(os.getenv("NMG_PORT", "8000"))

# Admin password (change this before event)
ADMIN_PASSWORD = os.getenv("NMG_ADMIN_PASSWORD", "admin123")

# Gameplay
START_CASH = float(os.getenv("NMG_START_CASH", "100000"))
TICK_SECONDS = float(os.getenv("NMG_TICK_SECONDS", "1.0"))

# News reaction window (market keeps drifting while this timer runs)
REACTION_SECONDS = int(os.getenv("NMG_REACTION_SECONDS", "45"))

# Price movement calibration (percent range for each intensity, total move over REACTION_SECONDS)
INTENSITY_RANGES = {
    "LOW":    (0.01, 0.02),
    "MEDIUM": (0.03, 0.05),
    "HIGH":   (0.06, 0.09),
}

# Spillover weights
DIRECT_WEIGHT = 1.00       # directly affected tickers
SECTOR_WEIGHT = 0.35       # same-sector spill
LINKED_WEIGHT = 0.18       # linked-sector spill
MARKET_NOISE_PCT = 0.0012  # per tick small random move (0.12%)

# Optional sector links (spillover)
SECTOR_LINKS = {
    "Tech":        ["Telecom", "Industrials"],
    "Banking":     ["Consumer", "RealEstate"],
    "Energy":      ["Industrials", "Consumer"],
    "Healthcare":  ["Consumer"],
    "Consumer":    ["Banking", "Industrials"],
    "Telecom":     ["Tech"],
    "Industrials": ["Energy", "Consumer"],
    "RealEstate":  ["Banking"],
}
