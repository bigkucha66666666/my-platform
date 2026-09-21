# Dynamic Bottleneck Cash Payment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The user requested implementation in this session and previously said not to create Git commits; execute inline and omit all commit steps.

**Goal:** Pay completed dynamic-bottleneck Human participants CNY 15–35 from their own 30-round mean cost, targeting CNY 25 at mean cost 20, with matching page and oTree admin records.

**Architecture:** Store each Human's formal cost total at round 33, compute a versioned Decimal cash settlement in `payment_info`, and post an idempotent oTree payoff adjustment in the final payment app. Persist audit fields in participant variables and custom export, without new database columns; keep per-round experimental points and the other scenarios intact.

**Tech Stack:** Python 3.10, oTree 5, unittest, oTree Bot, HTML templates.

---

## File map

- `settings.py`: dynamic formal cash config and CNY native currency code.
- `dynamic_bottleneck_round/__init__.py`: formal-only cost aggregation and participant settlement inputs.
- `dynamic_bottleneck_round/tests.py`: warmup-exclusion and final-round record tests.
- `payment_info/cash_payment.py`: pure validated calculation, Decimal rounding, formula version.
- `payment_info/__init__.py`: dynamic-only settlement, idempotent native payoff adjustment, participant-variable audit data and custom export.
- `payment_info/PaymentInfo.html`: dynamic cash receipt and breakdown, legacy points branch.
- `payment_info/test_cash_payment.py`: pure formula and final-page contract tests.
- `payment_info/tests.py`: oTree Bot assertion for final payment records.

### Task 1: Formal cost record

- [x] Add `test_formal_cost_total_excludes_warmup` with costs `[99, 98, 10, 20]` at rounds `[1, 3, 4, 5]`, expected 30; add a final-round Bot assertion that the participant cost total equals the sum of 30 formal `round_player.total_cost` values.
- [x] Run `/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.tests -v`; confirm the new helper/record assertions fail.
- [x] Implement `formal_cost_total(player)` parallel to `formal_payoff_total`; at the existing last-round `set_results` branch save `dynamic_bottleneck_round_total_cost`, mean cost, and settled formal count in `participant.vars`.
- [x] Rerun the focused tests and confirm green.

### Task 2: Pure cash formula

- [x] Add tests for `calculate_cash_payment(total_cost, formal_rounds=30)` at total costs 0, 600, 1200, 603 and an extreme value; assert CNY 35, 25, 15, half-up cent rounding, and monotonicity. Reject missing/negative/nonfinite cost and wrong round count.
- [x] Run `/opt/anaconda3/envs/otree_env/bin/python -m unittest payment_info.test_cash_payment -v`; confirm failures come from the missing calculation.
- [x] Implement `payment_info/cash_payment.py` using `Decimal(str(value))`, `ROUND_HALF_UP`, `bonus=max(0,min(20,20-total_cost/60))`, base 15 and version `dynamic_cost_v1`. Return an immutable settlement record with all audit values.
- [x] Rerun formula tests and confirm green.

### Task 3: Final payment integration

- [x] Add tests asserting dynamic final page exposes the CNY breakdown, `final_total_payoff` still stores experiment points, and repeated settlement sets `player.payoff` to the same adjustment while `participant.payoff_plus_participation_fee()` equals the cash total; assert non-dynamic behavior unchanged. Add a missing-cost test that sets manual-review status and never awards a performance bonus.
- [x] Run focused tests and confirm failure.
- [x] Set dynamic formal `participation_fee=15`, `real_world_currency_per_point=0.01`, `cash_payment_rule='dynamic_cost_v1'`; set global real-world code to CNY. Store formal cost, bonus, cash, version, status in participant variables and a payment custom export; adding Player fields breaks existing oTree databases. On dynamic final submission, compute cash and set `payment_info.Player.payoff = performance_bonus_cents - (participant.payoff - payment_info.Player.payoff)`; this makes native payoff exactly the bonus and is retry-safe. Missing record gets no bonus and a manual-review flag. Keep legacy scenarios on their current branch.
- [x] Render a dynamic-only cash receipt with CNY total, CNY 15 base, CNY performance bonus, 30-round mean cost and version; keep legacy receipt unchanged.
- [x] Explain the exact bounded payment formula on the formal-start page before paid decisions, but only for Sessions using the cash rule.
- [x] Rerun tests and inspect `git diff --check`.

### Task 4: Flow and regression verification

- [x] Run `/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.tests payment_info.test_cash_payment -v` and the existing Agent tests.
- [x] Run a dynamic formal/pilot oTree Bot path (no external LLM API) and verify final cash in the final page plus native oTree payment record; run the single-bottleneck/route-payment regressions.
- [x] Review the focused diff for preservation of other scenarios, no Git commit, and the provisional reference-cost note in the spec.

## Self-review

All requirements in the design are covered by one of the four tasks. The payment adjustment is isolated to dynamic formal Sessions, and formula inputs come from actual Human formal costs. The reference cost is explicitly provisional; observed mean payment requires pilot calibration.

## Execution and review (2026-09-18)

- The four implementation tasks are complete without a Git commit or database reset. Payment audit fields use participant variables and `payment_info` custom export, preserving the existing SQLite schema.
- An independent code review identified four edge cases; all were fixed: decimal per-round accumulation, payout posting on page display, 30-round settled-state validation, and restored mean-cost export.
- 276 relevant unit tests pass; existing single-bottleneck Bot and dynamic H-I0, H-I1, HA-I0, HA-I1 pilot Bots pass with no external LLM request. A 30-Human H-I0 Bot run produced mean cost 10.466 and mean pay CNY 29.77, and all 30 native oTree amounts match the payment export.
- The 30-Human Bot behavior is not a Human pilot calibration; the cost-20 reference remains provisional. The old `route_choice_prod` Bot cannot run because that app has no `tests.py`; its legacy payment branch is covered by a focused unit test.
