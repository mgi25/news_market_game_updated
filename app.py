import json
import random
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import config

app = Flask(__name__)
app.secret_key = getattr(config, "SECRET_KEY", "news-market-game-secret")

DATA_DIR = Path("data")
MAX_ROUNDS = 30
START_CASH = float(getattr(config, "START_CASH", 100000))
HOST = getattr(config, "HOST", "0.0.0.0")
PORT = int(getattr(config, "PORT", 5000))
DEFAULT_REACTION_WINDOW = int(getattr(config, "REACTION_SECONDS", 25))
ADMIN_PASSWORD = getattr(config, "ADMIN_PASSWORD", "admin123")

state_lock = threading.Lock()
rng = random.Random()


def _load_companies() -> List[Dict]:
    with open(DATA_DIR / "companies.json", "r", encoding="utf-8") as fh:
        companies = json.load(fh)
    valid = []
    for raw in companies:
        if "ticker" not in raw or "name" not in raw:
            continue
        valid.append(
            {
                "ticker": str(raw["ticker"]).upper(),
                "name": raw.get("name", raw["ticker"]),
                "sector": raw.get("sector", "General"),
                "start_price": float(raw.get("start_price", 100.0)),
            }
        )
    return valid


def _normalize_impact(item: Dict) -> Dict:
    impact = item.get("impact") or {}
    if not impact:
        impact = {
            "type": "sector" if item.get("sectors") else "company" if item.get("tickers") else "market",
            "target": (item.get("tickers") or [None])[0]
            or (item.get("sectors") or [None])[0],
            "direction": item.get("direction", "up"),
            "magnitude": {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(str(item.get("intensity", "LOW")).upper(), 1),
        }
    return {
        "type": impact.get("type", "market"),
        "target": impact.get("target"),
        "direction": str(impact.get("direction", "up")).lower(),
        "magnitude": float(impact.get("magnitude", 1)),
    }


def _load_news() -> List[Dict]:
    with open(DATA_DIR / "news.json", "r", encoding="utf-8") as fh:
        news = json.load(fh)
    normalized = []
    for idx, item in enumerate(news, start=1):
        normalized.append(
            {
                "id": item.get("id", f"N{idx:03d}"),
                "headline": item.get("headline") or item.get("title") or f"News {idx}",
                "description": item.get("description") or item.get("summary") or "",
                "body": item.get("body") or item.get("description") or item.get("summary") or "",
                "impact": _normalize_impact(item),
            }
        )
    return normalized


COMPANIES = _load_companies()
NEWS = _load_news()
TICKER_MAP = {c["ticker"]: c for c in COMPANIES}
SECTOR_GROUPS: Dict[str, List[str]] = {}
for c in COMPANIES:
    SECTOR_GROUPS.setdefault(c["sector"], []).append(c["ticker"])


def _base_prices() -> Dict[str, float]:
    return {c["ticker"]: c["start_price"] for c in COMPANIES}


GAME = {
    "round": 0,
    "max_rounds": MAX_ROUNDS,
    "market_open": False,
    "round_end": None,
    "reaction_window": DEFAULT_REACTION_WINDOW,
    "next_news_idx": 0,
    "current_news": None,
    "game_over": False,
    "prices": _base_prices(),
    "prev_prices": _base_prices(),
    "players": {},
}


def ok(**kwargs):
    payload = {"ok": True}
    payload.update(kwargs)
    return jsonify(payload)


def err(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def get_player_by_session():
    player_id = session.get("player_id")
    if not player_id:
        return None
    return GAME["players"].get(player_id)


def compute_player_totals(player):
    holdings_value = 0.0
    unrealized = 0.0
    for ticker, h in player["holdings"].items():
        price = GAME["prices"].get(ticker, 0.0)
        holdings_value += price * h["qty"]
        unrealized += (price - h["avg_cost"]) * h["qty"]
    total = player["cash"] + holdings_value
    return holdings_value, unrealized, total


def get_leaderboard():
    board = []
    for p in GAME["players"].values():
        _, unrealized, total = compute_player_totals(p)
        board.append(
            {
                "name": p["name"],
                "total": round(total, 2),
                "realized_pnl": round(p["realized_pnl"], 2),
                "unrealized_pnl": round(unrealized, 2),
            }
        )
    board.sort(key=lambda row: row["total"], reverse=True)
    return board


def _round_timer_left():
    if not GAME["market_open"] or not GAME["round_end"]:
        return 0
    return max(0, int(GAME["round_end"] - time.time()))


def _close_market_if_needed():
    if GAME["market_open"] and GAME["round_end"] and time.time() >= GAME["round_end"]:
        GAME["market_open"] = False


def _impact_multiplier(ticker: str, impact: Dict):
    m = max(0.2, min(3.0, float(impact.get("magnitude", 1))))
    sign = 1 if impact.get("direction") == "up" else -1
    impact_type = impact.get("type")
    target = impact.get("target")

    if impact_type == "market":
        return sign * 0.008 * m
    if impact_type == "company" and target and ticker == str(target).upper():
        return sign * 0.03 * m
    if impact_type == "sector":
        if target and TICKER_MAP[ticker]["sector"].lower() == str(target).lower():
            return sign * 0.02 * m
    return 0.0


def _apply_round_price_update(news_item: Dict):
    GAME["prev_prices"] = dict(GAME["prices"])
    impact = news_item["impact"]
    for ticker, old in GAME["prices"].items():
        drift = rng.uniform(-0.004, 0.004)
        shock = _impact_multiplier(ticker, impact)

        if impact.get("type") == "company" and impact.get("target") and ticker != str(impact.get("target")).upper():
            if TICKER_MAP[ticker]["sector"] == TICKER_MAP.get(str(impact.get("target")).upper(), {}).get("sector"):
                shock += shock * 0.45
        elif impact.get("type") == "sector" and impact.get("target"):
            if TICKER_MAP[ticker]["sector"].lower() == str(impact.get("target")).lower():
                shock += shock * 0.15

        move = max(-0.15, min(0.15, drift + shock + rng.uniform(-0.006, 0.006)))
        GAME["prices"][ticker] = max(1.0, round(old * (1 + move), 2))


def _snapshot(player=None):
    _close_market_if_needed()
    prices = []
    for c in COMPANIES:
        ticker = c["ticker"]
        price = GAME["prices"][ticker]
        prev = GAME["prev_prices"][ticker]
        change_pct = ((price - prev) / prev * 100) if prev else 0
        prices.append(
            {
                "ticker": ticker,
                "name": c["name"],
                "sector": c["sector"],
                "price": price,
                "change_pct": round(change_pct, 2),
            }
        )

    payload = {
        "round": GAME["round"],
        "max_rounds": GAME["max_rounds"],
        "market_open": GAME["market_open"],
        "timer": _round_timer_left(),
        "game_over": GAME["game_over"],
        "news": GAME["current_news"],
        "prices": prices,
        "leaderboard": get_leaderboard(),
    }
    if player:
        holdings_value, unrealized, total = compute_player_totals(player)
        payload["portfolio"] = {
            "cash": round(player["cash"], 2),
            "holdings": player["holdings"],
            "holdings_value": round(holdings_value, 2),
            "realized_pnl": round(player["realized_pnl"], 2),
            "unrealized_pnl": round(unrealized, 2),
            "total": round(total, 2),
            "transactions": player["transactions"][-10:],
        }
    return payload


@app.get("/")
def index():
    return render_template("index.html", title="News Trading Game")


@app.post("/join")
def join():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Please enter a player name.", "error")
        return redirect(url_for("index"))

    with state_lock:
        player_id = str(uuid.uuid4())
        GAME["players"][player_id] = {
            "id": player_id,
            "name": name[:24],
            "cash": START_CASH,
            "holdings": {},
            "realized_pnl": 0.0,
            "transactions": [],
        }
        session["player_id"] = player_id
    flash(f"Welcome, {name}!", "success")
    return redirect(url_for("game"))


@app.get("/game")
def game():
    with state_lock:
        player = get_player_by_session()
        if not player:
            flash("Player session missing. Please join the game first.", "error")
            return redirect(url_for("index"))
        return render_template("game.html", title="Trading Floor", player_name=player["name"])


@app.get("/admin")
def admin():
    return render_template("admin.html", title="Admin Console")


@app.get("/presenter")
def presenter():
    return render_template("presenter.html", title="Presenter Screen")


@app.get("/api/bootstrap")
def api_bootstrap():
    return ok(companies=COMPANIES, max_rounds=MAX_ROUNDS, reaction_window=GAME["reaction_window"])


@app.get("/api/state")
def api_state():
    with state_lock:
        player = get_player_by_session()
        if not player:
            return err("Player not found. Rejoin from home.", 401)
        return ok(state=_snapshot(player))


@app.get("/api/presenter/state")
def api_presenter_state():
    with state_lock:
        movers = sorted(_snapshot()["prices"], key=lambda x: abs(x["change_pct"]), reverse=True)[:5]
        return ok(state=_snapshot(), movers=movers)


@app.post("/api/trade")
def api_trade():
    payload = request.get_json(silent=True) or {}
    ticker = str(payload.get("ticker", "")).upper()
    side = str(payload.get("side", "")).upper()
    qty = int(payload.get("qty", 0) or 0)

    with state_lock:
        _close_market_if_needed()
        player = get_player_by_session()
        if not player:
            return err("Player not found. Rejoin from home.", 401)
        if GAME["game_over"]:
            return err("Game has ended. Trading is disabled.", 400)
        if not GAME["market_open"]:
            return err("Market is closed. Wait for next round.", 400)
        if ticker not in GAME["prices"] or side not in {"BUY", "SELL"} or qty <= 0:
            return err("Invalid trade payload.", 400)

        price = GAME["prices"][ticker]
        holding = player["holdings"].setdefault(ticker, {"qty": 0, "avg_cost": 0.0})

        if side == "BUY":
            cost = price * qty
            if player["cash"] < cost:
                return err("Insufficient cash.", 400)
            old_qty = holding["qty"]
            player["cash"] -= cost
            holding["qty"] += qty
            if holding["qty"] > 0:
                holding["avg_cost"] = ((holding["avg_cost"] * old_qty) + cost) / holding["qty"]
        else:
            if holding["qty"] < qty:
                return err("Cannot sell more shares than owned.", 400)
            proceeds = price * qty
            player["cash"] += proceeds
            holding["qty"] -= qty
            pnl = (price - holding["avg_cost"]) * qty
            player["realized_pnl"] += pnl
            if holding["qty"] == 0:
                del player["holdings"][ticker]

        player["transactions"].append(
            {
                "ts": int(time.time()),
                "ticker": ticker,
                "side": side,
                "qty": qty,
                "price": price,
            }
        )
        return ok(message="Trade executed.", state=_snapshot(player))


@app.post("/api/admin/login")
def api_admin_login():
    payload = request.get_json(silent=True) or {}
    if payload.get("password") != ADMIN_PASSWORD:
        return err("Unauthorized.", 401)
    session["is_admin"] = True
    return ok(message="Admin authenticated.")


def _admin_guard():
    return bool(session.get("is_admin"))


@app.get("/api/admin/state")
def api_admin_state():
    with state_lock:
        if not _admin_guard():
            return err("Unauthorized.", 401)
        players = [
            {"id": p["id"], "name": p["name"], "cash": round(p["cash"], 2)} for p in GAME["players"].values()
        ]
        return ok(state=_snapshot(), players=players, reaction_window=GAME["reaction_window"])


@app.post("/api/admin/reaction_window")
def api_admin_reaction_window():
    payload = request.get_json(silent=True) or {}
    with state_lock:
        if not _admin_guard():
            return err("Unauthorized.", 401)
        seconds = int(payload.get("seconds", GAME["reaction_window"]))
        GAME["reaction_window"] = max(10, min(60, seconds))
        return ok(reaction_window=GAME["reaction_window"])


@app.post("/api/admin/advance_round")
def api_admin_advance_round():
    with state_lock:
        if not _admin_guard():
            return err("Unauthorized.", 401)
        _close_market_if_needed()
        if GAME["game_over"]:
            return err("Game is already finished.", 400)
        if GAME["market_open"]:
            return err("Current round still open.", 400)

        if GAME["round"] >= MAX_ROUNDS:
            GAME["game_over"] = True
            return err("All 30 rounds are complete.", 400)

        news_item = NEWS[GAME["next_news_idx"] % len(NEWS)]
        GAME["next_news_idx"] += 1
        GAME["round"] += 1
        GAME["current_news"] = news_item
        _apply_round_price_update(news_item)
        GAME["market_open"] = True
        GAME["round_end"] = time.time() + GAME["reaction_window"]

        if GAME["round"] >= MAX_ROUNDS:
            # 30th round opens now; it will become game_over when window ends.
            pass

        return ok(state=_snapshot())


@app.post("/api/admin/start")
def api_admin_start():
    with state_lock:
        if not _admin_guard():
            return err("Unauthorized.", 401)
        GAME["game_over"] = False
        GAME["market_open"] = False
        GAME["round"] = 0
        GAME["round_end"] = None
        GAME["current_news"] = None
        GAME["next_news_idx"] = 0
        GAME["prices"] = _base_prices()
        GAME["prev_prices"] = _base_prices()
        for p in GAME["players"].values():
            p["cash"] = START_CASH
            p["holdings"] = {}
            p["realized_pnl"] = 0.0
            p["transactions"] = []
        return ok(state=_snapshot())


@app.post("/api/admin/reset")
def api_admin_reset():
    with state_lock:
        if not _admin_guard():
            return err("Unauthorized.", 401)
        GAME["players"] = {}
        GAME["game_over"] = False
        GAME["market_open"] = False
        GAME["round"] = 0
        GAME["round_end"] = None
        GAME["current_news"] = None
        GAME["next_news_idx"] = 0
        GAME["prices"] = _base_prices()
        GAME["prev_prices"] = _base_prices()
        session.clear()
        return ok(message="Game reset complete.")


@app.before_request
def enforce_round_end_state():
    with state_lock:
        _close_market_if_needed()
        if GAME["round"] >= MAX_ROUNDS and not GAME["market_open"]:
            GAME["game_over"] = True


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
