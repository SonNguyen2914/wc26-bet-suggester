# World Cup 2026 Kalshi Bet Suggester — Project Documentation & Roadmap

> **SUPERSEDED BY V8** (`docs/V8/PROJECT_DOC.md`, Jul 23) — V7 remains as the
> evaluation-closing snapshot; the expansion-arc changes (two-plane architecture,
> PostgreSQL live plane, mls-2026-v0, full Kalshi market coverage) live in V8.

**V7 — July 22, 2026. THE HARDENED EDITION.** V6 closed the tournament arc; V7 closes
the **evaluation arc**: in the 24 hours after V6 was written, the project was measured
(the full calibration + significance battery), independently reviewed (an external
technical/quantitative/product evaluation), corrected (four real defects fixed same-day
with regression tests), locked down (fail-closed read-only API with authenticated
operator controls), made self-healing (deploy wipes rebuild ALL state unattended), and
re-credentialed (every secret rotated onto fresh infrastructure). The system V7
describes is what the evaluation asked for: **a well-tested, read-only public research
archive with protected operator controls, corrected model logic, deterministic evidence
reporting, and explicitly documented limitations.** Docs live in the repo
(`docs/V7/`); V6 remains in place as the tournament-closing snapshot; V1–V4 are ghosts,
V5 survives on branch `docs-v5-handoff`.

---

## ⚡⚡⚡⚡ CURRENT STATE — V7 SNAPSHOT (July 22, 2026)

### The one-paragraph version
A completed-tournament research platform, now in its archival configuration: the public
API is **fail-closed read-only** (every mutation 403s without the operator token), every
deploy **self-heals completely** (results, bracket, closings, AND the settled bot ledger
rebuild at boot from feeds + committed artifacts — no rituals, no operator), all alert
channels are **rotated and verified end-to-end** (two-channel Discord split + secret
ntfy topic, per-leg delivery probing), the model carries the evaluation's corrections
(**xg_model v2**: centered set-piece, per-draw first-goal, all-in-cost Kelly, bid-side
exits), and every quantitative claim is **pinned to a reproducible, seeded pipeline**
whose honest headline is: suggestively better than chance, statistical parity with the
exchange, calibration competitive under every specification. Backend Python 3.12/
FastAPI on Railway; frontend Next.js/TS on Vercel at namson.dev/bet-suggester (the
four-league champions'-gold shell, unchanged since V6).

### Where things stand right now
- **Repos:** backend `~/dev/wc26-bet-suggester` @ `1385d9e` (**327 tests green**, 103
  commits, ~9.2k LOC src + ~4.6k LOC tests); frontend `~/dev/namson-dev` @ `aeb522c`
  (102 commits). Local == remote == deployed.
- **Jul 22 evening — V7-evaluation BUILD round** (see V7.1 addendum): canonical
  lock serving (F1), side-effect-free public GETs + strict fail-closed boolean
  (F2), live first-goal interval fix (F3), integer red-card counts (F4), unified
  fee economics (F5), corrected corpus/interview claims (F6), `/api/ready`,
  isolated test database, 347 tests green.
- **Prod, verified Jul 22 ~08:50–09:30 UTC** (`scripts/verify_lockdown.sh`, ALL CHECKS
  PASSED): every OpenAPI mutation 403 anonymous; operator paths 200 with the token;
  expensive-route limiter 429s; ledger 84/84 self-healed in ~1 min; champion Spain;
  all three alert legs delivered (`action/detail/ntfy_delivered: true`); API-Football
  `key_configured: true` on the NEW key.
- **Credentials, all rotated Jul 22:** `ADMIN_TOKEN` live (operator copy at
  `~/.wc26_admin_token`, chmod 600 — scripts auto-send it); fresh secret `NTFY_TOPIC`
  (env-only, never committed; the tournament topic is dead); Discord two-channel
  split (ACTION = renamed original channel, DETAIL = `#wc26-detail`; legacy
  `DISCORD_WEBHOOK_URL` variable deleted); new API-Football key under Son's
  **dedicated project gmail** (fresh account; the lost account's exposed key still
  awaits deactivation if ever recoverable).
- **Model evaluation, final language (Part I + `docs/V7/CALIBRATION.md`):** 11/14
  knockout winner calls (one-sided p=0.029, two-sided 0.057 — suggestive); Brier
  0.0898 vs 0.0911 over 293 frozen markets = parity with the executable ask; ECE
  binning-sensitive; AUC identical; the live KELLY gate's flat replay NEGATIVE
  (−11.2%) — published beside the bot's +45% bankroll pilot result.

### The two days in one list (what V7 added over V6)
1. **The calibration + significance battery** — 293 frozen markets scored against
   backfilled Kalshi settlement truth (100% coverage); the 14-match knockout
   scorecard (6 prospective-frozen locks + 8 labeled git-archaeology
   reconstructions; CAN_MAR/PAR_FRA honestly excluded — the repo's first commit
   post-dates their kickoffs); cluster bootstraps, both-sided binomial, multi-spec
   ECE, AUC — all in one deterministic script with pinned outputs.
2. **The independent evaluation** — an external review of the full project archive.
   Verified here claim-by-claim: it found **four real defects our own audits missed**
   (set-piece double counting, first-goal mixture math violating Jensen,
   fee-incomplete Kelly sizing, ask-side exit valuation), correctly identified the
   bot leaderboard as a two-match pilot dominated by correlated positions, showed
   the ECE headline was binning-sensitive, and called out claim inflation. Nothing
   material in it was false. Its central sentence — *"the engineering system is
   currently stronger than the evidence for market edge"* — is this project's
   official position.
3. **Model corrections, all regression-tested (xg_model v2):** set pieces enter as a
   competition-mean-CENTERED adjustment (mitigates — does not fully eliminate — the
   overlap with total-xG attack; decomposition needs re-extracted inputs, the
   extracted corpus carries set-play counts only); first-goal/no-goal computed PER
   latent-rate draw (the mean-rate shortcut discarded the gamma mixture's zero
   mass); KELLY gates AND sizes at all-in cost (price + entry fee). Archived
   tournament predictions remain v1 outputs — the scorecard scores what was
   actually frozen.
4. **Bid-side execution:** `_market_yes_bid` (bid → derived from the no-ask →
   legacy cents → None, NEVER the ask), persisted per OddsReading (new column +
   migration); WIRE exits/take-profits fill at the bid or hold; tracker cash-outs
   at the bid with a **NO_BID** verdict when absent; equity marks bid-first. An
   absent bid means NOT EXECUTABLE — the ask is never silently substituted.
5. **The fail-closed lockdown:** `PUBLIC_READ_ONLY` defaults TRUE (an absent or
   misspelled variable leaves the API read-only, never open); mutations need
   `X-Admin-Token` or `Authorization: Bearer` compared via `secrets.compare_digest`;
   empty/malformed credentials always fail; auth is evaluated BEFORE the rate
   bucket; the one mutating GET (`force_refresh`) is operator-gated; expensive
   routes 429 inside `RATE_LIMIT_SECONDS`. `scripts/verify_lockdown.sh` sweeps
   every OpenAPI mutation mechanically — no remembered-endpoint lists.
6. **Fully self-healing deploys:** the settled ledger joined the boot self-heal
   (`src/bots.py::restore_from_archive` reads the committed canonical export) —
   a wipe now rebuilds results + bracket + closings + bot state in ~1–2 minutes
   with ZERO API calls. Proven live twice on Jul 22. The manual restore endpoint
   remains as a token-gated fallback. The export→push→restore ritual (proven ×12
   in its lifetime) is retired.
7. **Alert stack rotation + per-leg probing:** `/api/alerts/test` now reports
   `action/detail/ntfy_delivered` booleans — which immediately caught the last
   bug of the arc: a **trailing newline smuggled into NTFY_TOPIC by terminal
   copy-paste** made every ntfy publish fail silently. Fixed permanently by
   stripping ALL secret env vars at load. All three legs verified delivered,
   phone ping confirmed 09:07 UTC.
8. **Honest-statistics infrastructure:** the pinned tests carry HISTORICAL-ARTIFACT
   semantics (assertions bind to `input_version: wc26-final-2026-07-21`; a future
   dataset that clears zero is a report update, not a failure); the evidence
   hierarchy (prospective-frozen / reconstructed / descriptive-replay /
   pilot-strategy-result / execution-comparison) labels every claim in every doc.
9. **Credential hygiene:** dedicated project gmail owns new service accounts;
   secrets live in env + `~/.wc26_admin_token` only; committed defaults for
   secret-bearing vars are gone (ntfy fail-closed empty).

---

## PART A — ARCHITECTURE (V7 delta map — full map in V6 Part A, still accurate)

### Backend `~/dev/wc26-bet-suggester` (Python 3.12, FastAPI, SQLAlchemy/SQLite, APScheduler)
```
config.py                 V7: PUBLIC_READ_ONLY (default TRUE — fail closed),
                          ADMIN_TOKEN, RATE_LIMIT_SECONDS; ALL secret-bearing
                          vars .strip()ed at load (the ntfy newline lesson);
                          NTFY_TOPIC has NO default (fail-closed empty)
api/main.py               V7: _admin_ok (compare_digest, X-Admin-Token OR
                          Bearer) + _public_guard middleware (403 mutations in
                          read-only, auth before rate bucket, 429 on expensive
                          prefixes); force_refresh GET operator-gated;
                          /api/alerts/test reports PER-LEG delivery booleans;
                          /api/bots/restore delegates to src.bots (fallback);
                          equity marks bid-first
src/models/xg_model.py    v2-centered-setpiece: SET_PIECE_BASELINE 0.236
                          (pinned to the stats table by test); honest-scope
                          comment (mitigates, not eliminates)
src/models/simulator.py   first-goal/no-goal per latent-rate draw (Jensen fix)
src/bots.py               kelly_entries at all-in cost q = c + fee(c);
                          wire_exits at the BID (no bid -> hold);
                          restore_positions + restore_from_archive (boot
                          self-heal from the committed canonical export)
src/positions.py          cash-outs at the bid; NO_BID verdict when absent;
                          alert templates quote the bid
src/kalshi_client.py      _market_yes_bid (never falls back to the ask);
                          live + demo rows carry "yes_bid"
src/db.py                 OddsReading.yes_bid column + forward migration
src/alerts.py             unchanged shape; send_discord/send_ntfy return
                          delivery booleans consumed by the test probe
jobs/scheduler.py         boot chain: restore_results -> restore_ledger ->
                          resolve_bracket -> prime (the wipe is a non-event)
scripts/score_calibration.py   THE deterministic statistics pipeline (seed 26,
                          input_version wc26-final-2026-07-21) -> emits
                          research_archive/calibration_results.json
scripts/verify_lockdown.sh     mechanical acceptance matrix from OpenAPI
tests/                    327 green. conftest.py opts tests out of read-only
                          (the dev posture); test_evaluation_fixes.py (22
                          regression tests for the four defects + lockdown);
                          test_calibration_pipeline.py (historical-artifact
                          pins — narrative drift fails the suite)
research_archive/         + settlements_backfill_2026-07-21.json (100%
                          settlement truth), knockout_recon_2026-07-21.json,
                          calibration_scored_rows.json, calibration_results.json
```

### Frontend `~/dev/namson-dev` — unchanged since V6 (@ `aeb522c`)
The four-league drive-mode shell, champions'-gold WC26 theme, flipped bracket
pyramid. V6 Part A remains the reference. (The tracker's renamed API fields —
`bid`/`cashout_now` nullable — touch no frontend code; the tracker was always
alert-only.)

### Deployment & ops (the V7 posture)
- **Railway**: auto-deploys on push to main. The DB wipe is now a NON-EVENT:
  boot self-heal rebuilds everything unattended. No archive-first ritual, no
  restore call, no settings reprime — push and walk away.
- **Operator access**: mutations need the token (`~/.wc26_admin_token`; scripts
  send `X-Admin-Token` automatically). `scripts/verify_lockdown.sh [BASE]` is
  the one-command health check.
- **Alerts**: action channel + detail channel (complete log, receives action
  copies BY DESIGN) + ntfy phone push for action-kind only. Prove with the
  token-gated `/api/alerts/test` — trust `*_delivered`, not silence.
- **Vercel** (frontend): unchanged, no DB anywhere near it.

## PART B — THE MODEL (v2, as now deployed)
The V6 Part B formulas stand with these corrections:
1. **Set pieces (v2):** `xg = open_play + (set_piece_threat − 0.236)` — a
   competition-mean-centered adjustment that preserves average expected goals
   and mitigates, but does not fully eliminate, overlap with total-xG attack
   inputs. Full decomposition is next-competition work (needs set-piece xG,
   which the extracted PMSR corpus does not carry — counts only).
2. **First-goal race:** P(no goal), P(team first) computed per gamma draw and
   averaged — preserving the mixture's zero mass; the three outcomes sum to 1
   by construction. cv=0 recovers the old shortcut exactly (tested).
3. **KELLY (and SNIPER):** edge gate AND Kelly fraction at all-in unit cost
   q = price + 0.07·price·(1−price). Integer-contract/nonlinear-fee optimal
   sizing (maximize expected log wealth over feasible n) remains future work.
4. **Execution semantics:** buys at the ask + fee; exits/cash-outs at the bid
   − fee; no bid = not executable. Forecast benchmarking vs execution
   comparison are now vocabulary-level distinct (Part I).
IMPORTANT: every archived tournament prediction is a **v1 output** — the
scorecard and calibration score what was actually frozen, not v2 retro-runs.

## PART C — LIVE PIPELINE
Unchanged cadences (V6 Part C). One addition: the odds poll persists
`yes_bid` per reading, so the next competition's research corpus carries both
sides of the book from day one. The full lock schema (bid/ask/depth/
timestamps/fee version/run id) is specified in Part H as the next-league gate.

## PART D — THE BOT ARENA (final framing)
Rules as in V6 Part D, with KELLY/SNIPER now all-in-cost (future seasons) and
WIRE exiting at the bid. **The 2026 leaderboard is a pilot-strategy-result**:
a two-match settled window (THIRD + FINAL — earlier history died in
pre-procedure wipes), the winner's P&L four correlated England contracts plus
one loss, fills ask-side optimistic (they predate the bid fix). KELLY
+454.73 first, SHEEP −119.25 last — the ordering matched the thesis and is
dominated by one match direction; the live KELLY gate flat-staked across all
six lock matches is **−11.2%**. Both sentences are published together,
always. Next-season arena requirements (frozen definitions, decision traces,
cluster-level reporting, seed distributions for COIN) are in Part H.

## PART E — RESEARCH SYSTEM (complete + measured)
V6 Part E stands with ONE correction (V7 evaluation F6): the corpus is **six
complete prospective market-level lock/closing/result bundles** (NOR_ENG,
ARG_SUI, SF1, SF2, THIRD, FINAL) + **eight labeled reconstructed winner
calls** + two excluded matches — NOT "all 16 triples"; ESP_BEL/MAR_FRA have
closings and results but their locks died pre-discipline. Serving is now
CANONICAL (V7 evaluation F1): `src/archive.py` reads the committed bundles
directly — the research endpoint and finished-match review pages fall back
to them when DB rows are absent, and the retrospective-simulation fallback
is REMOVED (a lock-less match says `archive_incomplete`, honestly).
`/api/ready` reports archival completeness distinctly from liveness.
V7 adds the OUTPUT layer:
- `settlements_backfill_2026-07-21.json` — settlement truth for 100% of the
  293 locked markets (Kalshi keeps settled markets queryable).
- `knockout_recon_2026-07-21.json` — the 8 pre-lock matches re-simulated with
  the exact commit deployed at each kickoff (labeled reconstructions).
- `calibration_scored_rows.json` + `calibration_results.json` — the scored
  join and the full seeded statistics artifact (input_version
  wc26-final-2026-07-21).
- `docs/V7/CALIBRATION.md` — the write-up: three-stream scoring, per-family
  tables, the 14-match scorecard, the significance battery, both replays, the
  evidence hierarchy, every caveat.

## PART F — OPS RUNBOOK (V7: mostly deleted, deliberately)
- **Deploy:** push to main. That's the whole runbook — the wipe self-heals.
- **Verify:** `scripts/verify_lockdown.sh` (add the base URL for non-prod).
- **Operator mutation:** any curl with `-H "X-Admin-Token: $(tr -d '\r\n' <
  ~/.wc26_admin_token)"`.
- **Alerts:** `POST /api/alerts/test` (token) → check the three `*_delivered`
  booleans; the phone gets exactly ONE probe message (action-kind reality
  alerts fan to phone; detail-kind never does).
- **Gated pushes:** unchanged — never pipe pytest (exit-code masking).
- **Remote Control:** unchanged lineage quirk (V6 Part F) — standalone
  Terminal, `claude --resume`, may take two tries; keep the Mac awake
  (`caffeinate -ims` for dark-screen-but-alive).

## PART G — SECURITY & CREDENTIALS (new part)
- **Model:** public = read-only; operator = shared-secret header over TLS.
  Right-sized for a single-operator personal tool; NOT a multi-user design
  (no roles, no audit log, process-local rate limiting — see Part H).
- **Fail-closed principle:** read-only is the default state; secrets have no
  committed defaults; secret-bearing env vars are stripped at load (a
  terminal-newline in NTFY_TOPIC silently killed pushes for an hour on
  Jul 22 — the probe caught it, the strip killed the class).
- **Rotation log (Jul 22):** ADMIN_TOKEN created (never displayed — clipboard
  + file only); ntfy topic regenerated (old public topic dead); Discord split
  webhooks fresh, legacy variable deleted; API-Football key new, under the
  dedicated project gmail.
- **Owed:** deactivate the OLD API-Football key if its lost account is ever
  recovered; the repo's git history still contains the dead tournament ntfy
  topic (harmless — topic retired — noted for completeness).

## PART H — KNOWN DEBT & THE NEXT-LEAGUE GATE (post-evaluation, honest)
**Structural (the evaluation's P1/P2, adopted as the MLS/EPL entry gate):**
1. Durable persistence — the self-heal is excellent operationally AND proof
   that deploys still destroy primary state; a real Postgres + Alembic +
   backups is the fix. T-10 locks remain the one unrestorable class
   (mitigated by archive-at-creation).
2. Full market-book lock schema — bid/ask/depth/last/timestamps/fee-version/
   sim-seed/run-id per observation, so forecast benchmarking is
   reconstructable (this tournament's market stream is the executable ask).
3. Prediction run_id provenance + DB-level idempotency (locks, bot pins).
4. Set-piece decomposition (open-play vs set-piece xG inputs) or joint
   estimation; integer/nonlinear-fee Kelly; suggester EV fee-inclusion.
5. Frontend automated tests (scenario math first); dependency lockfile; CI
   pipeline (the repo has none — "locally test-gated" is the honest label).
6. Distributed scheduling/rate limiting/idempotency before ANY multi-replica
   deployment; structured logging + retention policy for season-long data.
7. Public README (still the highest-leverage presentation gap) + screenshots.
8. Deferred quality items: ESPN "HT" ET-interval clamp; COIN/TILT/SCHOLAR
   in-memory pin drift; `_classify_outcome`/match-page decomposition;
   VAR-momentum linger; Son's macOS update (now outliving TWO doc editions).
**The competitions umbrella** (`~/dev/competitions/`): MLS in-season now,
EPL/La Liga from mid-August; league deltas (home advantage, draws, no
knockout damping, rosters, per-league calibration) scoped in each README.
**MLS DATA LAYER LIVE (Jul 22 evening):** `src/mls.py` — self-contained,
DB-less, cache-backed; keyless ESPN usa.1 (scoreboard/schedule/standings)
+ Kalshi KXMLSGAME 3-way books and KXMLSCUP futures (bid AND ask captured
from day one); four read-only `/api/mls/*` endpoints (archive-compatible);
the MLS page renders the live slate, real books, fixtures, and conference
tables. NO model, NO suggestions — the acceptance gates above still gate
that, and the page says so.

## PART I — THE REPORT CARD (final, one screen)
Full tables + methods: `docs/V7/CALIBRATION.md`. Reproduce:
`.venv/bin/python scripts/score_calibration.py`.
```
winner calls      11/14 (one-sided p .029, two-sided .057)   suggestive
Brier             raw .0898 | anchored .0896 | ask .0911     parity (CIs straddle 0)
ECE               .0269 vs .0384 @10-bin width               binning-sensitive
AUC               .893 vs .890                               identical
family Brier      model ahead 7/9                            broad, not cherry-picked
replays           raw-edge +3.0% | LIVE KELLY GATE -11.2%    descriptive only
bot arena         KELLY 1st, SHEEP last, 2 matches           pilot, correlated
the call          Spain 53.9% at freeze -> champions         the story, not the proof
```
**Claim discipline:** parity + calibration-competitive + suggestive winner
record + one champion call. Never "proven", never "beat the market", never
"better-calibrated than the exchange" — those died in review, on purpose.

---

## V7.1 ADDENDUM — THE EVALUATION CHRONICLE (Jul 21 evening – Jul 22 morning)
The sequence, for the record: Son commissioned the calibration write-up → the
scorecard → the significance battery ("evaluate the model's precision") → an
independent external evaluation of the full archive. The evaluation was
verified here finding-by-finding (hashes checked, its statistics script read,
its claims tested against the code): four real defects confirmed, zero
material errors found in it. Patch series round 1 same evening (model fixes,
lockdown build, reproducible statistics, claim rewrite); round 2 on the
evaluator's refinements (bid-side execution, fail-closed default, ledger
self-heal, historical-artifact semantics). Jul 22 morning: Son's one-window
env batch (token, topic, split webhooks, new API-Football key on a fresh
dedicated project gmail, legacy webhook variable deleted) → the acceptance
matrix passed end-to-end → the last bug of the arc (the ntfy newline) was
caught by the new per-leg probe and killed class-wide → phone ping confirmed
09:07 UTC. Twenty-six hours from "evaluate it" to "everything the evaluation
asked for, deployed and verified."

**Jul 22 evening — the V7 evaluation and the BUILD round.** A second
independent evaluation reviewed the V7 archive itself (hashes verified —
byte-identical to the shipped zip) and found the remediation real but
incomplete in exactly the ways that matter for an archive: the T-10 locks
were NOT in the boot self-heal — verified live, the deployed site was
serving `final_lock: 0` and a current-model retro-simulation on the FINAL's
review page (F1); anonymous GETs could persist prediction rows and the
read-only boolean parsed "true " as OPEN (F2 — the same whitespace class as
the ntfy newline, one day later); the live first-goal path passed
full-match rates, freezing no-goal at 9.1% through minute 89 (F3); red-card
columns were Boolean and a second red raised StatementError (F4); the
primary suggester still gated on gross edge (F5); and the docs claimed "all
16 triples" where six complete bundles exist (F6). ALL SIX fixed in one
build round: `src/archive.py` serves the committed bundles canonically and
the retro-sim fallback is gone (lock-less matches say `archive_incomplete`);
public GETs are side-effect-free with 403 on unauthorized refresh intent;
the boolean parser fail-closes on anything but an exact off-value;
first-goal runs on remaining-interval lambdas (ET/FG parameters split so it
cannot recur) and disappears once a goal scores; red cards are integer
counts end-to-end; `src/execution.py` is the single economics module
(suggester edge/EV now net of fees — the evaluator's 0.55@0.50 marginal
example is a pinned test); `/api/ready` reports archival completeness; the
test suite runs on an isolated database; and every corpus/interview claim
was rewritten to the 6+8+2 truth. Suite: 347 green.

**Doc lineage:** V1–V4 ghosted (Desktop, Jul 17); V5 = pre-final handoff
(branch `docs-v5-handoff`); V6 = the tournament-closing snapshot (evolved
in-place through the evaluation, then superseded); **V7 (this file) = the
hardened, evaluated, archival state — and the baseline the next league
builds from.** Docs live in the repo. Nothing project-critical touches the
Desktop. Ever.
