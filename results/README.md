# results/

Machine-readable outputs behind the tables in the README and the posts. Every file is produced by a script in the repository root; the command is in that script's docstring.

| file | produced by | what it is |
|---|---|---|
| `event_study_results.csv` | `event_study.py` | conditional returns (bp) at 1/2/5/15/60 min after each event type, by symbol, side, regime and hour; thinned events |
| `crypto_walls_results.csv`, `walls_results.csv` | `run_walls_crypto.py`, `run_walls.py` | order-book detectors, walk-forward, maker vs taker (Binance 2026, FORTS 2020) |
| `trend_daily_results.csv`, `trend_daily_equity.csv` | `trend_daily.py --source moex` | trend rule on all nine FORTS families, data to 18 Sep 2026: returns by year and by instrument for the 60/120/250 ensemble, and daily equity of every variant (ensemble, single windows, vol-scaled long, equal-weight buy and hold). Counted from the ensemble's first day in the market (31 Jan 2019), the equity file gives the README figure: Sharpe 0.83, t 2.3, +6.7%/yr at 8.3% vol. The crypto runs and the window maps are tabulated in [posts/2026-09-trend-daily.md](../posts/2026-09-trend-daily.md) (RU) |
| `funding_scan_episodes.csv`, `funding_scan_by_year.csv` | `funding_scan.py` | all extreme-funding episodes (symbol, start, end, payments, entry rate, gross, net) and the yearly summary (columns below) |
| `backtest_results.csv`, `walk_forward_results.csv`, `portfolio_*.csv`, `optimization_results.csv`, `risk_comparison.csv`, `stops_comparison.csv`, `ensemble_results.csv`, `satellite_*.csv`, `weights_*.csv` | `trading_analyzer/` runners | the early daily-bar strategies on stooq data (divergences, round levels, correlation, ensembles) — weak or overfit, kept for completeness |

Two files keep the Russian column names the scripts write:

- `funding_scan_by_year.csv`: `эпизодов` episodes; `в_плюсе` share of episodes with positive net, %; `медиана_net_pct` median net per episode, % of notional; `сумма_net_pct` sum of net over all episodes, % of notional; `взято` episodes taken by the capital-constrained simulation ($10k, three equal slots); `сим_usd` that simulation's P&L, USD; `верх_usd` upper bound if every episode were taken at $10k notional, USD. There is no 2022 row: no episode met the entry rule that year.
- `trend_daily_equity.csv`: `ансамбль 60/120/250` ensemble of the three windows; `один N=…` a single window of N days; `всегда лонг (вол-норм.)` always long, vol-scaled; `B&H равные доли` equal-weight buy and hold.

Raw market data (ClickHouse archive, minute bars, funding history) is not in the repository; the fetchers rebuild the public parts.
