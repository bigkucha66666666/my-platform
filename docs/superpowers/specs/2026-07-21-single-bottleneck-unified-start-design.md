# Single Bottleneck Unified Start Design

## Goal

Add a synchronized start gate between the first-round comprehension check and the first decision page. Participants must not lose decision time because they finish the introductory steps at different speeds.

## Participant Flow

The first-round flow becomes:

`Introduction -> ComprehensionCheck -> UnifiedStartWait -> Decision -> ResultsSync -> Results`

`UnifiedStartWait` is an oTree `WaitPage` displayed only in round 1. It waits at the subsession level so every participant in the session, including participants assigned to different bottleneck groups, is released at the same time. Later rounds retain the existing flow and do not show this page.

The waiting page uses concise Chinese copy explaining that the formal experiment will start after everyone reaches the page. It must not start or reduce the decision timer while participants are waiting.

## Timing Semantics

The common decision deadline is created only after the unified wait gate releases. Existing decision timeout logic remains unchanged after that point. This guarantees that the first participant entering `Decision` receives the full configured decision duration rather than inheriting a deadline created by an earlier page visit.

## Administrator Recovery

The project will use oTree's authenticated Session Monitor rather than exposing `OTREE_REST_KEY` or adding a custom state-changing endpoint.

The single-bottleneck admin report will include an `Open unified-start control` action for the selected session. It opens that session's Session Monitor in a new tab and explains that the administrator can use oTree's `Advance slowest participants` control when a participant never reaches the gate. Once all participant slots have reached the wait page, oTree releases the session normally.

This recovery path may require more than one advance action when an absent participant is several pages behind. The report must state this explicitly and must not claim that one click directly releases the experiment.

## Scope

- Applies only to `single_bottleneck`.
- Applies only before round 1.
- Synchronizes the entire session, not individual groups.
- Adds no model fields and requires no database reset.
- Does not change grouping, cost, toll, payoff, dropout, or result logic.
- Does not expose REST credentials to participant or administrator templates.

## Testing

- Bot flow must submit the new wait page between `ComprehensionCheck` and `Decision` in round 1.
- Later rounds must not include the wait page.
- The waiting template must contain the unified-start explanation.
- The admin report must contain the Session Monitor control and accurate recovery guidance.
- Existing staggered and same-time bot cases must complete all rounds.
- Python compilation and `git diff --check` must pass.

