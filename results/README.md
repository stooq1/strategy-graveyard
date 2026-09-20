# results/

Machine-readable outputs behind the tables in the README and the posts. Every file is produced by a script in the repository root; the command is in that script's docstring.

| file | produced by | what it is |
|---|---|---|
| `event_study_results.csv` | `event_study.py` | conditional returns (bp) at 1/2/5/15/60 min after each event type, by symbol, side, regime and hour; thinned events |
| `crypto_walls_results.csv`, `walls_results.csv` | `run_walls_crypto.py`, `run_walls.py` | order-book detectors, walk-forward, maker vs taker (Binance 2026, FORTS 2020) |
| `trend_daily_results.csv` | `trend_daily.py` | trend rule by year and by instrument (last run: Binance perps with funding) |
| `funding_scan_episodes.csv`, `funding_scan_by_year.csv` | `funding_scan.py` | all extreme-funding episodes (symbol, start, end, payments, entry rate, gross, net) and the yearly summary |
| `backtest_results.csv`, `walk_forward_results.csv`, `portfolio_*.csv`, `optimization_results.csv`, `risk_comparison.csv`, `stops_comparison.csv`, `ensemble_results.csv`, `satellite_*.csv`, `weights_*.csv` | `trading_analyzer/` runners | the early daily-bar strategies on stooq data (divergences, round levels, correlation, ensembles) — weak or overfit, kept for completeness |

Raw market data (ClickHouse archive, minute bars, funding history) is not in the repository; the fetchers rebuild the public parts.
