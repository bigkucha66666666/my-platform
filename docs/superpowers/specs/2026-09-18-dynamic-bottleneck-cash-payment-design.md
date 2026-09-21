# Dynamic Bottleneck Cash Payment Design

## Scope and decision

The user approved a target of roughly CNY 25 for a completed, approximately 30-minute experiment and a CNY 15 participation component plus up to CNY 20 determined by performance. This change applies to the formal dynamic-bottleneck Session, including its independent pilot composition mode. The demonstration Session and other experimental scenarios retain their payoff rules. No Git commit is requested.

## Alternatives considered

- A constant exchange rate on the existing 30-round point total is simple, but cannot give a fixed CNY 15 floor and CNY 35 ceiling while centering reliably near CNY 25.
- Within-Session ranking would make one person's pay depend on treatment-group composition and other participants' outcomes; this would weaken comparisons.
- Use the same absolute-cost formula for every Human in every dynamic-bottleneck treatment (chosen). The rate and reference are frozen in each Session config, independent of group membership or Agent count.

## Formula and records

Let `C` be the Human participant's total choice cost over all 30 formal rounds and `c = C / 30`. The three warmup rounds and all Agent costs are excluded. For a completed Human participant:

`performance_bonus = clamp(20 - 0.5 × c, 0, 20)` CNY,

`cash_total = 15 + performance_bonus` CNY.

Amounts round only at the final cent, half up. Per-round stored cost values are converted individually to decimal strings before summation, so binary float accumulation cannot shift a half-cent boundary. Thus `c = 20` pays CNY 25, `c = 0` pays CNY 35, and `c >= 40` pays CNY 15. The reference cost of 20 is provisional; it is not an estimate from pilot data, and the rule does not guarantee an observed mean of CNY 25. Any post-pilot recalibration should be frozen before formal recruitment and recorded as a new rule version.

Keep the current 140-minus-cost per-round points as experimental feedback and export them unchanged. Record formal total and mean cost, total points, base amount, performance amount, total CNY, and payment-rule version in participant variables and a custom payment export. Validate that all 30 formal rounds have settled before writing the payment input. Do not add payment-app database columns: existing oTree databases do not auto-migrate and must not be reset for this change. The final page displays the CNY amount and its breakdown, while other scenarios still display their current point totals. Before paid rounds begin, the formal-start page discloses the formula. As soon as the final page opens, its cash amount is posted as an idempotent accounting adjustment in hundredths-of-CNY point units, with a CNY 15 participation fee, so the displayed receipt and oTree's native Session payment agree even if the participant leaves before page submission. The project's real-world currency code must be CNY because oTree has one global currency-code setting; this changes the currency symbol of other scenarios, not their numerical rules.

If the 30 settled formal costs are unavailable, do not silently award maximum performance. Mark settlement for manual review and show no calculated cash total. The normal completed flow always produces the cost record before the payment page.

## Verification

Unit tests cover the reference point, cap and floor, lower-cost monotonicity, cent rounding, warmup exclusion, and missing-cost safety. Bot/integration tests verify that dynamic final-page CNY amount, native oTree payment total, persisted breakdown, and exported record agree and that non-dynamic payment behavior is unchanged. The pilot Session uses the same rule without requiring 30 Human participants.

The 30-Human deterministic Bot run produced mean cost 10.466 and mean pay CNY 29.77. Bots are not representative of human departure choices; this is a code-level sanity check, not calibration evidence. The provisional mean-cost-20 reference remains until Human pilot data are available.
