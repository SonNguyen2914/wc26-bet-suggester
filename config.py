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
# 0.45 matches the value production always ran with (the old 0.60 default
# forced a manual settings re-POST after every deploy, since the SQLite
# settings row is wiped with the DB). Now a redeploy needs no manual step:
# the boot-time prime job repopulates predictions and this default is
# already the operating value.
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.45"))
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

# --- Knockout goal damping -------------------------------------------------
# Knockout matches score fewer goals than group matches (well documented:
# e.g. WC2018 group stage averaged 2.54 goals/match with knockout 90-minute
# averages lower — teams protect leads with elimination on the line). The
# DIRECTION is sourced; the exact 0.85 per-team multiplier remains an
# estimate, kept configurable so it can be tuned against data without code.
KNOCKOUT_DAMPING = float(os.getenv("KNOCKOUT_DAMPING", "0.85"))

# --- Goal overdispersion ---------------------------------------------------
# Real goal counts are streakier than an independent Poisson allows —
# variance/mean ~1.1-1.25 in the literature, and this tournament's tails
# agree (four 3-0s, three 1-4s in 41 clean matches). A gamma-mixed Poisson
# (negative binomial): per-team-match rate multipliers Gamma(k, 1/k) with
# CV = this value; CV 0.30 at lambda 1.3 gives variance/mean ~1.12. What it
# DOES: fattens blowout tails and 0-0, better longshot/total calibration.
# What it DOESN'T: fix any 1-0-vs-1-1 ordering — dispersion slightly RAISES
# one-nil mass (zero-side convexity) and trims 1-1; the top-of-list order
# for even matchups is the calibrated answer either way. The tournament's
# apparent one-nil deficit (4 seen vs 7.6 expected) is p~0.13 — noted, not
# actionable. Set to 0 to recover pure Poisson.
GOAL_DISPERSION_CV = float(os.getenv("GOAL_DISPERSION_CV", "0.30"))

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

# --- Live match feed (Layer 2: API-Football) ------------------------------
# Optional. When API_FOOTBALL_KEY is set, the /live endpoint can auto-fetch
# the real score/minute/red-cards instead of the user typing them. Free tier
# is 100 requests/day, so calls are budgeted: a hard daily cap (stop before
# the limit) plus a short cache so repeated reads of the same match don't
# each cost a request. World Cup is league id 1 in API-Football.
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE = os.getenv("API_FOOTBALL_BASE", "https://v3.football.api-sports.io")
API_FOOTBALL_LEAGUE_ID = int(os.getenv("API_FOOTBALL_LEAGUE_ID", "1"))
API_FOOTBALL_SEASON = int(os.getenv("API_FOOTBALL_SEASON", "2026"))
API_FOOTBALL_DAILY_CAP = int(os.getenv("API_FOOTBALL_DAILY_CAP", "90"))  # < 100
API_FOOTBALL_CACHE_SECONDS = int(os.getenv("API_FOOTBALL_CACHE_SECONDS", "20"))
# An EMPTY live=all answer (free-plan season blindness, or genuinely nothing
# live) is re-checked gently instead of every cache window — the dedicated
# live tick would otherwise burn the daily cap on calls that return nothing.
# ESPN carries the live read during the backoff.
LIVE_EMPTY_BACKOFF_SECONDS = int(os.getenv("LIVE_EMPTY_BACKOFF_SECONDS", "900"))
# The live-state snapshot tick — decoupled from the (slow, minutes-long)
# odds poll so the scoreboard tracks the real match closely.
LIVE_TICK_SECONDS = int(os.getenv("LIVE_TICK_SECONDS", "15"))

# In-play BUY/SELL signals on watched markets: fire when |live model −
# market price| clears the threshold; re-fire only on a side flip or when
# the divergence grows another 5 points, never inside the cooldown.
LIVE_SIGNAL_MIN_DIFF = float(os.getenv("LIVE_SIGNAL_MIN_DIFF", "0.08"))
LIVE_SIGNAL_COOLDOWN_SECONDS = int(os.getenv("LIVE_SIGNAL_COOLDOWN_SECONDS", "180"))
LIVE_SIGNAL_POLL_SECONDS = int(os.getenv("LIVE_SIGNAL_POLL_SECONDS", "30"))

# EASY-WIN alerts scan EVERY open in-play book (not just watched ones): the
# live model must call it near-certain, the price must still leave a real
# payout, and the gap must show the market hasn't fully caught up yet.
LIVE_EASYWIN_MIN_PROB = float(os.getenv("LIVE_EASYWIN_MIN_PROB", "0.85"))
LIVE_EASYWIN_MAX_PRICE = float(os.getenv("LIVE_EASYWIN_MAX_PRICE", "0.90"))
LIVE_EASYWIN_MIN_DIFF = float(os.getenv("LIVE_EASYWIN_MIN_DIFF", "0.05"))

# --- Live-state tracking (scoreboard robustness + finished-match handling) --
# A live match briefly disappears from API-Football's live=all during
# between-periods breaks (90'->ET, ET->penalties). The scoreboard holds a
# match through gaps up to this long before treating it as finished; must
# exceed the longest break (halftime-before-ET + ET->pens can be ~20 min).
LIVE_GAP_GRACE_MINUTES = int(os.getenv("LIVE_GAP_GRACE_MINUTES", "25"))
# How long a finished match stays on the live scoreboard as an FT card before
# dropping to the Past-matches section only.
LIVE_FT_WINDOW_MINUTES = int(os.getenv("LIVE_FT_WINDOW_MINUTES", "60"))
# How early before kickoff the live feed starts being polled for a match (the
# poll trails until TRACK_HOURS_AFTER_KICKOFF past kickoff). Kept tight so the
# daily API-Football budget is spent near/during matches, not days ahead on
# knockout fixtures that are "trackable" 96h out only for Kalshi market pricing.
LIVE_POLL_LEAD_MINUTES = int(os.getenv("LIVE_POLL_LEAD_MINUTES", "15"))

# --- Bracket auto-resolution ---------------------------------------------
# How often to check finished R16 (then QF, SF) results and fill the next
# round's placeholder slots. Low frequency by design: the bracket changes at
# most a handful of times all tournament, and the job self-skips (zero feed
# calls) once every slot is resolved, so this is nearly free.
BRACKET_RESOLVE_MINUTES = int(os.getenv("BRACKET_RESOLVE_MINUTES", "30"))

# --- Position tracker + alert fan-out ------------------------------------
# Cash-out-vs-hold verdicts flip when the better side wins by this fraction
# of the position's cost (hysteresis against book wobble).
POSITION_FLIP_MARGIN = float(os.getenv("POSITION_FLIP_MARGIN", "0.05"))
# ntfy.sh topic for instant phone pushes, independent of Remote Control and
# any open page. The default topic ships in a PUBLIC repo — treat it as a
# temporary channel for the final and rotate/override via env after.
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "wc26-son-final-kj7f2p9qx4")
