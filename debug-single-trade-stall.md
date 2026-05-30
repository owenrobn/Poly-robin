[OPEN] Debug Session: single-trade-stall

Goal
- Fix: bot does not take another trade after the first one.

Environment
- OS: Windows
- Processes: python main.py (server+bot), python signaler.py

Hypotheses
- A: RiskControls blocks subsequent trades (killed/master disabled/maxTrades reached/market already active).
- B: Trades never close/unregister (on_done not called, pending close/flip loops, position stuck).
- C: Pocket reports not ready or reserved funds never released, preventing new trades.
- D: Ingestion/market mapping issues: new market_id not resolved or active_markets missing after first.
- E: UI/profile switching causes params to block (maxTrades too low/cooldown huge), or start_trade not called.

Plan
1) Start Debug Server and collect instrumentation logs (pre-fix).
2) Reproduce: take first trade, then attempt second trade via UI/coin-toggle or signal path.
3) Analyze evidence and implement minimal fix.

Notes
- Instrumentation uses Debug Server events (no print/console.log).
