# Dynamic Bottleneck Decision and Results Visual Polish

## Scope

Only restyle:

- `dynamic_bottleneck_round/Decision.html`
- `dynamic_bottleneck_round/Results.html`
- Relevant template-contract assertions in `dynamic_bottleneck_round/tests.py`

Do not change models, calculations, service-rate draws, toll calibration, time-wheel behavior, result data, exports, grouping, timeouts, or `single_bottleneck`.

## Visual Direction

Use the approved “soft commute scene” direction:

- Deep teal for primary actions and travel information.
- Warm orange only for tolls, queue segments, and the selected participant.
- Off-white and pale blue-green surfaces instead of flat white.
- Rounded cards between 14 and 18 pixels, subtle borders, and restrained shadows.
- Vehicle graphics are functional indicators, not decorative repetitions.

## Decision Page

### Capacity panel

- Replace the rigid left-border card with a rounded information panel.
- Keep the actual capacity or candidate-capacity states exactly as currently controlled by `capacity_revealed`.
- Add one compact inline road-and-car illustration on the right.
- Keep the round indicator as a dark rounded pill.
- On mobile, the illustration moves below the capacity text without reducing text width.

### Time picker

- Preserve the existing hidden input, choice rows, toll labels, JavaScript selection logic, keyboard behavior, and vertical scroll container.
- Increase the card radius and spacing.
- Convert the previous/next controls to small circular arrow buttons visually embedded beside the wheel.
- Give the selected time a dark teal surface and show the toll as a compact warm badge.
- Keep non-selected rows quiet so that the selected time remains the dominant focus.
- Restyle the submit button to match the teal commute palette.

## Results Page

### Overview and journey

- Keep the current combined “cost and travel time” information architecture.
- Round the outer overview card and soften section boundaries.
- Keep total cost prominent and capacity information visually separate.
- Add one small vehicle marker above the fixed-travel/queue bar to clarify that the bar represents the participant’s trip.
- Do not add a separate bottleneck route illustration.
- Retain only the current two travel metadata items: same-minute participant count and total travel time.

### Cost and group chart

- Restyle cost items as a connected but softly rounded group.
- Preserve all five cost components and all text.
- Preserve chart data, average line, count bands, current-choice highlight, and horizontal scrolling.
- Improve card radius and background contrast without adding vehicles inside the chart.

## Accessibility and Responsive Behavior

- Inline vehicle SVGs use `aria-hidden="true"`.
- Existing labels, button names, keyboard controls, and result semantics remain.
- At widths below 760 pixels, cards become single column, capacity illustration stacks, and cost items remain readable.
- No horizontal overflow outside the existing chart scroll container.
- Motion is limited to existing smooth wheel scrolling; no continuous vehicle animation.

## Verification

- Template tests assert the new visual hooks and vehicle SVG hooks.
- Existing dynamic app unit and bot tests must remain green.
- Python compilation and `git diff --check` must pass.
- Browser review checks desktop and mobile widths for overlap, overflow, and selection clarity.

