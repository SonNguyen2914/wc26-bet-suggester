# V9 P0 Remediations — response to the third independent evaluation

**July 23, 2026.** A third independent evaluation of the
`wc26-project-2026-07-23-V9.zip` artifact raised 21 findings (F1–F21) plus
a documentation/claim audit. The evaluation was accurate and fair — every
finding we checked was a correct reading of the source. This document is
the finding-by-finding response: what was built, where, and — where a
claim was stronger than the code proved — the exact narrowed claim the
code now backs.

**Nothing here changed the safety posture.** Every finding was about
research-grade rigor and execution-validity, not a money leak.
`REAL_MONEY_SIGNALS_ENABLED=false`, no code path enables real money, and
the model output stays labeled *shadow / not advice*. The controlling
statistical fact is unchanged and still front-and-centre: the model's
edge over a naive league/venue baseline is **+0.008 log-loss, 95% CI
[−0.012, +0.029] — not significant.** Shadow approval means "safe to
collect prospective evidence," never "edge established."

Migration head after this pass: **`f9a1c0d2b3e4`**. Backend: **441 tests
green + 5 real-PostgreSQL run in CI** (the one network-dependent lineup
test is now hermetic). The migration round-trips empty→head→down on real
`postgres:16`, and the partial unique index `uq_fixture_canonical_t10`
survives it intact.

## P0 — built

| # | Finding | What was done | Where |
|---|---------|---------------|-------|
| **F1** | Boot approval used a bare Monte-Carlo point estimate (`model_mls.backtest(2000)`), bypassing the CI evaluator | Boot now runs `model_eval.evaluate_ladder` (analytic + bootstrap CI), persists an **immutable `ModelApprovalDecision`** (metrics, edge + 95% CI, limitations, content hash), and sets `approved_for_shadow` **from that record**. Policy: shadow is approvable when the sample clears a floor and the model is not *significantly worse* than baseline — a CI spanning zero is fine for evidence collection, never for an edge claim. | `model_eval.ensure_approval_decision`, `shadow_approval_policy`; `jobs/scheduler.mls_boot`; `models.ModelApprovalDecision` |
| **F2** | A lineup fetch failure returned `None`; the lock proceeded and then failed its own `lineup_snapshot_referenced` audit (also the one non-hermetic test) | `capture_lineup` now **always persists a snapshot** — `fetch_failed` on a network error — so a canonical lock never references a null lineup. The audit test is hermetic (canned summary). New test asserts the fetch-failure invariant directly. | `lineups.capture_lineup`, `_record_unavailable`; `tests` |
| **F5** | `lineup/market/team/player/availability_snapshot_id` on `prediction_run` were plain integers, no FKs | Real **foreign keys** (PostgreSQL-native, `ON DELETE RESTRICT`) for `lineup_snapshot_id` and `market_snapshot_id`; a new FK for the approval-decision link. team/player/availability stay documented **reserved** columns (no backing tables), and the earlier availability=lineup conflation (**F14**) was removed. | `models.PredictionRun`; migration `f9a1c0d2b3e4` |
| **F6** | Registry discovery (`/events`, `/markets`) was single-page; only lock capture paged | `discover_and_map` and `_ensure_contracts` now use the cursor-complete `_kalshi_paged`, which reports `cap_reached`; a truncated sweep is recorded (`discovery_complete: false`, `truncated_series`) instead of silently returning short. | `markets.discover_and_map`, `_ensure_contracts`, `_kalshi_paged` |
| **F7** | Prices → integer cents, sizes → integer contracts at ingest; subpenny/fractional evidence rounded away | Exact provider values retained **beside** the derived cents: `*_dollars` price strings + `sizes_fp_json` on `market_quote`, `price_dollars`/`size_fp` per depth level. The integer-cent columns stay the executable comparator. | `models.MarketQuote`/`MarketDepthLevel`; `markets._quote_row`, `_depth_levels`, `_dollars_str`, `_sizes_fp` |
| **F8** | Paper fee rounded Kalshi's fee *per contract* then multiplied — wrong, and sign-flipped by price | `order_fee_c` implements the versioned order-level rule `ceil(0.07·C·P·(1−P))` on the whole fill, applied to the signal (at target size) and the actual fill. Series/event overrides + maker/taker still unmodeled, so paper P&L is **labeled approximate**. | `paper.order_fee_c`, `FEE_SCHEDULE` |
| **F9** | Missing provider timestamps read as age 0 / "fresh" | Freshness is computed over the **required game quotes** with an explicit `freshness_basis` (`provider` / `capture_time` / `none`); the `oldest_age == 0` escape is gone. A missing timestamp falls back to capture time, labelled as such. | `markets.capture_lock_snapshot`; `models.MarketSnapshot` |
| **F3** | The public corpus was rebuilt from live state each call — same version label, different bytes | Publishing freezes one version's bytes + manifest into an immutable `corpus_export` row; `/api/mls/corpus?version=…` serves **from storage**, `?preview=1` is a clearly-labelled unpublished build. Re-publishing a version is refused. | `corpus.publish_corpus`/`get_published`/`list_published`; `models.CorpusExport`; `POST /api/admin/mls/corpus/publish` |
| **F4** | Replay imported current engine constants — a later constant change moved probabilities silently | The input artifact (now `model-input-v2`) freezes an **engine signature** (set-piece, dispersion CV, red-card constants, xg-model + numpy versions). `verify_replay` compares it and **refuses** on drift rather than replaying under a different engine. | `model_mls.engine_signature`, `build_input_artifact`, `replay_from_artifact`; `audit.verify_replay` |
| **F17** | `/api/ready` didn't require shadow-readiness for the served mode | Mode-specific readiness: `archive_ready` / `shadow_collection_ready` / `paper_execution_ready`; top-level `ready` reflects the served mode; shadow blockers now include a missing approval decision. | `api.ready`; `runs.shadow_counts` |
| **F16** | The frontend mixed a frozen T-10 model with the current book without labeling the two moments | A **temporal-basis** panel names the four objects (canonical T-10 model, T-10 frozen book, latest diagnostic model, current market book) with timestamps, and the edge is labelled "frozen/latest model vs CURRENT ask — two moments." | `mls/[eventId].tsx` `TemporalBasis` |

## Narrowed claims (the code now proves exactly this)

- **"Atomic T-10 lock" → "two-phase, completeness-gated T-10 lock"** (F11).
  The completed market snapshot commits before the run; a crash can orphan
  a snapshot but never fabricate a lock.
- **"Bit-identical from the bytes alone" → "bit-identical under the
  matching engine"** (F4). Replay verifies a frozen engine signature and
  refuses on drift.
- **"Immutable corpus"** now means an immutable *published* version served
  from stored bytes (F3) — the live builder is a labelled preview.
- **"Full book" → "top-10-depth, required-family (GAME 3-way) book, with
  exact fixed-point values retained"** (F7, F12).
- **"Execution-quality paper trading" → "depth-aware taker-entry,
  hold-to-settlement paper simulation with order-level general fees
  (approximate: no maker/taker or series/event overrides)"** (F8, F13).
- **"Availability plane"**: there is no injury/suspension feed;
  `AVAILABILITY_COMPLETE` is an honestly-labelled lineup-confirmation
  proxy, and the misleading `availability_snapshot_id` conflation is
  removed (F14).

## Documentation & config fixes

- **F9.9** `.env.example` now ships `PUBLIC_READ_ONLY=true` (was `false`) —
  a copied env can no longer reopen writes; dev opts out explicitly.
- **F9.7** `RELEASE-mls-shadow-v1.md` now clearly separates the frozen-tag
  state (`c21ba2ee8df4`) from the current branch (`f9a1c0d2b3e4`).
- **F9.8** The "436 tests" claim is corrected to **441 green + 5 PG**, and
  the one network-dependent test is hermetic, so the aggregate is honest.

## Deferred to P1 (not P0; explicitly out of scope for this pass)

These remain real and are recorded, not silently dropped:

- **F12** store all depth levels (currently policy `top_10_depth`).
- **F13** bid-side exits / order lifecycle / exchange reconciliation.
- **F14** a real availability/injury feed as its own entity.
- **F15** nested / holdout model selection; validate the deployed
  stochastic simulator (not only the analytic mean-rate core).
- **F18** implement (or document as informational) the stale-market kill
  switch — currently a deliberate `pass` for PAPER, with per-trade quote
  age still gating fills.
- **F19** route-specific limits + caching for the replay/corpus endpoints.
- **F20** distributed scheduler leases / idempotency across replicas.
- **F21** full raw provider payloads in object storage.

None of these is Saturday-blocking. The P0 set above is what makes the
first prospective slate research-grade.
