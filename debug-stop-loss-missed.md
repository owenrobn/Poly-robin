[OPEN] Debug Session: stop-loss-missed

## Symptom
- User losing money; stop loss did not trigger and/or bot did not place the close when SL price was reached.

## Expected
- When SL threshold is crossed, the bot should attempt to close the position immediately (and keep retrying until closed), and the UI should show the close attempts/results.

## Hypotheses (falsifiable)
1) Tick delivery stalled: strategy stopped receiving price updates, so SL condition never evaluated.
2) SL condition evaluated but close did not execute: sell order failed / matched=0 / order rejected.
3) Wallet balance fetch returned 0 on transient error; strategy falsely assumed it had no shares and marked the trade closed, leaving real position open (no more SL management).
4) Wrong price domain: SL is compared against the wrong side price (YES vs NO) so threshold is effectively unreachable.
5) Market expiry / shutdown path closed tracking early (trade removed from active list) while position remained open.

## Evidence Plan
- Capture: ticks (frequency), SL triggers, close attempts, sell post_order responses, get_order matched, token-balance reads (raw + exceptions), and any “sync closed” decisions.

## Repro Steps
- Keep bot running on a single market, enter a position, let price move through SL threshold.
- Observe debug logs + UI Activity feed.
