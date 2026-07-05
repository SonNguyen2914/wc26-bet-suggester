"""Central configuration. Everything overridable via environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# --- Mode ---------------------------------------------------------------
# DEMO_MODE=true runs the whole system on realistic mock Kalshi/sports data
# so you can develop and demo without API keys. Flip to false when you have
# real Kalshi credentials and WC26 markets exist.
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"

# --- Kalshi -------------------------------------------------------------
KALSHI_BASE_URL = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2")
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")

# --- Database -----------------------------------------------------------
# SQLite by default (zero setup). Point at Postgres when ready, e.g.:
#   DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/kalshi
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'suggester.db'}")

# --- Prediction engine --------------------------------------------------
N_SIMULATIONS = int(os.getenv("N_SIMULATIONS", "10000"))
PREDICTION_CACHE_TTL_SECONDS = int(os.getenv("PREDICTION_CACHE_TTL_SECONDS", "300"))  # 5 min
HOURLY_PREDICTION_WINDOW_HOURS = int(os.getenv("HOURLY_PREDICTION_WINDOW_HOURS", "6"))
FINAL_LOCK_MINUTES_BEFORE_KICKOFF = int(os.getenv("FINAL_LOCK_MINUTES", "10"))

# --- Suggestion filters (defaults; editable via /api/settings) ----------
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.05"))          # 5%
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.60"))
MIN_VOLUME_24H = float(os.getenv("MIN_VOLUME_24H", "10000"))

# --- Timing / ripeness alerts --------------------------------------------
ODDS_POLL_SECONDS = int(os.getenv("ODDS_POLL_SECONDS", "30"))
RIPENESS_ALERT_THRESHOLD = float(os.getenv("RIPENESS_ALERT_THRESHOLD", "75"))
RIPENESS_MIN_READINGS = int(os.getenv("RIPENESS_MIN_READINGS", "10"))
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "30"))

# --- Alerts -------------------------------------------------------------
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# --- API ----------------------------------------------------------------
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,https://namson.dev").split(",")

# --- Model humility (market anchoring) -----------------------------------
# Final probability = MODEL_WEIGHT * model + (1-MODEL_WEIGHT) * market-implied.
# Liquid markets are usually right; only large, genuine disagreements should
# survive the edge filter. Raise toward 1.0 as the model earns trust.
MODEL_WEIGHT = float(os.getenv("MODEL_WEIGHT", "0.60"))
MAX_ODDS = float(os.getenv("MAX_ODDS", "8.0"))       # skip lottery-ticket longshots
MAX_SUGGESTIONS_PER_MATCH = int(os.getenv("MAX_SUGGESTIONS_PER_MATCH", "3"))

# --- Ranking board (likelihood-first) -------------------------------------
# The board shows bets MOST LIKELY TO HAPPEN that the user can then judge by
# edge/multiplier themselves. Likelihood is the gate and the sort key; edge
# is informational only (never a filter). Two-tier floor: if nothing clears
# the primary floor across all matches, retry once at the fallback floor,
# then show an honest empty state (no further lowering).
SUGGEST_PRIMARY_FLOOR = float(os.getenv("SUGGEST_PRIMARY_FLOOR", "0.49"))
SUGGEST_FALLBACK_FLOOR = float(os.getenv("SUGGEST_FALLBACK_FLOOR", "0.40"))

# Keep tracking a match through kickoff (live odds move on goals) and stop
# only once it's truly done: kickoff + 4h covers 90 min + ET + pens + Kalshi
# book-settling. Applies to the scheduler, the poller, and the board.
TRACK_HOURS_AFTER_KICKOFF = float(os.getenv("TRACK_HOURS_AFTER_KICKOFF", "4"))
