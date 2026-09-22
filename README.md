# Strategy Graveyard

**Honest tests of retail trading edges on crypto perpetuals and MOEX futures — with the infrastructure that produced them.**

Russian version: [README.ru.md](README.ru.md) · Research journal (RU): [docs/FINDINGS.md](docs/FINDINGS.md), [docs/REVIEW.md](docs/REVIEW.md) · Write-ups: [posts/](posts/) · Telegram (RU): [t.me/strategy_graveyard](https://t.me/strategy_graveyard)

This repository is a four-month independent research project (June–September 2026) that set out to find a tradeable edge in market microstructure and cross-market lead-lag, and instead produced something rarer: a set of *negative results that can be trusted*. Every popular retail idea below was implemented, run through an event study, a walk-forward backtest with realistic costs, and a concentration test — and closed with a number, not an opinion.

The one-line summary: **at the one-minute horizon on liquid instruments, the ratio of edge to costs is 0.4–0.8 across four instruments, two venues, two market regimes and four different "leader" instruments.** The only time an edge appeared above costs was the March 2020 panic, when daily moves were 5%+. That is a property of the regime, not of any signal.

---

## Results at a glance

| Hypothesis (as retail believes it) | Data | Test | Result | Why it fails |
|---|---|---|---|---|
| **Order-book walls**: bounce off a wall, pulled wall = breakout, bid/ask imbalance | Binance USDT-M perps, 6 symbols (BTC…ATOM), depth20 snapshots every second, Jun–Sep 2026 | walk-forward, maker vs taker, regime gates, signal inversion | Negative after fees on every one of 4 runs and 6 symbols. Maker execution halves the loss but never creates a profit. Inverting the signals gives the same P&L (BTC −7.8% vs −8.5%) → the directional component is ≈ 0 | within ±0.035% of price there are no "walls" in crypto depth20; what remains is noise wrapped in fees |
| Same detectors on **FORTS 2020** (RTS index and USD/RUB futures, Feb–Sep 2020) | QUIK full order book (50 levels/side ≈ 1/s), 3.2 bn rows in ClickHouse | walk-forward with regime specialization | +20.5% total, median Sharpe 2.49 — but `wall_bounce` on RIH0 made +40% in the March crash alone | works only in a panic; the same code on calm 2026 crypto loses |
| **Aggressor flow, touches, "ate/pulled the wall", liquidation cascades, open interest** | FORTS 2020 + Binance 2026 (trades, forceOrder, OI) | event study, 1–60 min after event, events thinned | 0–3 bp vs 1–8 bp costs; single cells with t < 3 and no counterpart on the other side | nothing exploitable at 1-minute resolution |
| **Lead-lag BTC → alts**, "spring" (residual vs leader) | Binance 1-min, 2026 | event study | zero; SOL ← BTC k=3 +2.7 bp (t 2.3) vs 8 bp taker round trip | arbitraged within the minute |
| **Conductor S&P 500 → RTS futures (RI)**, 2020 | investing.com index feed + FORTS ticks, Mar–Sep 2020 | event study + trade structure with costs + session-boundary filter | +3.7 bp/event at 5 min (t 4.0, k=2, up only, hot regime only); the single tradeable cell is RI, k ≥ 3σ, up only: **net +7.15 bp, t 3.0, 66% of it from the top-5 days** (+2.62 without them) | real in 2020; by 2023–2026 the β of RI to S&P fell 44× (0.80 → 0.018) — that market no longer exists |
| Own-momentum control: leader = target | same | RI ← RI gives +2.2 bp (k=2); Si ← Si gives 0.0 | half of the RI "conductor effect" was RI's own momentum | mandatory control before crediting any leader |
| **Conductors S&P / Nasdaq / Brent / USD-CNH → USD/RUB and RTS futures, 2023–2026** | MOEX ISS 1-min (170k minutes) + Dukascopy | lead triage (β, lagged correlations), event study by quarter, tail test | USD/CNH → Si is the cleanest lag structure of the project (β +0.31, corr 0.047 → 0.019 → 0.011 → 0.005 forward, 0 backward) and pays **+0.4 bp (t 2.2) vs 0.5 bp round trip**; sign flips by quarter; top-5 days = 80% | information exists, magnitude is 3–10× below costs; the tail hypothesis (edge only on big days) is rejected: 2026 "big days" are 17× smaller than 2020's |
| Intraday **S&P → BTC** via tokenized SPY (Kraken/Gate xStocks) | own index collector, 9 days | event study, US session vs off-session, reverse causality control | +3.7 bp (t 1.3) in session, 0 off-session, no reverse effect; BTC↔SPY correlation lives entirely at lag 0 (0.40) | below the 8 bp taker round trip by construction; and the effect is intra-minute |
| **Trend following on dailies** (time-series momentum, vol-targeted, weekly rebalance) | Binance perps 2020–2026 with funding; stooq 2017–2026; all 9 liquid FORTS families 2019–2026 | pre-declared rule; window map 30–300 on both markets; costs stressed up to ×50; top-5 days cut from the benchmark as well; a pass/fail threshold for the crypto re-test written down before it was run | strong on FORTS (ensemble 0.83 at t 2.3; single N=30 reaches 1.29, and 1.34 at t 3.5 with Feb–Mar 2022 removed; the window shape replicates across two FORTS samples at rank correlation **+0.90**) — and absent on crypto (rank correlation **−0.12 / +0.03**, vol-scaled long beats all nine windows on 16 perps); the pre-declared crypto threshold was missed | it worked on one market in one regime, not as a general trend premium; on the only venue I can trade it is a diversifier (alpha 0.58–0.78 at ~zero correlation to long), not income — about 7%/yr at 10% vol |
| **Funding carry** (spot long + perp short) | Binance funding history 2019–2026, 16 majors | by year | BTC averaged +11.6%/yr (2021: +31%, 2022: +4%, 2026 YTD: +2%); worst funding drawdown −1.5% | real but regime-dependent; ≈ 0 in the current regime |
| **Extreme-funding spike arbitrage on small perps** (the one edge where small capital has an advantage) | **658 perps, 364 hedgeable**, full funding history 2019–2026 | pre-declared episode rule (enter after 3 payments ≥ 100%/yr, exit < 30%, 50 bp/episode), capital-constrained simulation, optimistic-entry bound | 1,125 episodes, 58% profitable; **2021: +1,000% of notional summed** (IOTA/TRB/ANKR +14–15% per 3-week episode), 2024 burst; **2023, 2025, 2026: negative** even with optimistic entry at any threshold | since 2023 funding settles every 4 h instead of 8, episodes last 0.5 days instead of 3 — bots harvest the spike within one or two payments |

Full numbers, tables and the reasoning behind every closure are in the journal ([docs/REVIEW.md](docs/REVIEW.md), sections 4–6.11; [docs/FINDINGS.md](docs/FINDINGS.md), sections 3–5) and in the machine-readable [results/](results/).

---

## Methodology — what made the negative results trustworthy

Most retail backtests are positive because of the method, not the market. The rules that made these results hold up:

1. **Event study before any backtest.** Conditional return in basis points, in the direction of the hypothesis, at 1/2/5/15/60 minutes after the event; events thinned to at most one per horizon; split by hot/calm regime and by hour; compared with tick + fee + half-spread. A hypothesis is alive only if the effect is ≥ 3–5× costs, t ≥ 3 after thinning, same sign in at least two of three periods.
2. **Walk-forward with non-overlapping test windows**, and the share of profitable windows reported. One window lied ("BTC maker +2%, Sharpe 9" became −4% on five).
3. **Concentration decomposition of every green cell**: share of the top-5 days, long vs short, by hour. An edge that is three days or one side of a rally is exposure, not edge.
4. **Own-momentum control**: rerun the conductor test with the leader replaced by the target itself.
5. **Signal inversion as a diagnostic**: if flipping the direction leaves P&L unchanged, the loss is costs plus noise, and there is nothing to flip.
6. **Costs modelled per side**: tick, fee, half-spread; a maker model with adverse selection (limit orders fill only when price actually reaches them, so the profitable runaways are lost).
7. **No lookahead**: signal on bar *i* executes at the open of bar *i+1*; reactive filters, ranking by future outcome and close-of-signal-bar execution are all named and avoided.
8. **Pre-declared rules; sensitivity ≠ optimization.** Parameter sweeps are reported as sensitivity tables, never used to pick the headline number.
9. **t-statistics, not annualized Sharpe, on short samples**: Sharpe 1.48 on 66 days is t = 0.63.
10. **Session-boundary filter** for instruments with trading breaks (the first bar of a FORTS session contains the whole overnight move of the leader; half of the "opening-hour events" were phantoms).

Details and worked examples: [docs/FINDINGS.md § 5](docs/FINDINGS.md), [docs/REVIEW.md § 3 and § 5](docs/REVIEW.md).

---

## Infrastructure

Everything below runs unattended on one small VPS (Docker Compose) and has been collecting since June 2026. Snapshot of 22 September 2026: 2.05 billion rows in ClickHouse after 109 days of collection, 8.3 GiB on disk (order-book snapshots compress 8×, trades 2.9×), about 80 MB a day on average.

| Component | What it does |
|---|---|
| [`collector/collector.py`](collector/collector.py) | Binance USDT-M futures websocket → ClickHouse: depth20 snapshots (1 s), aggregated trades, mark price + funding (1 s), forced liquidations (`forceOrder`), open interest (REST poll). Batched inserts, auto-reconnect, `restart: unless-stopped`. |
| [`collector/index_collector.py`](collector/index_collector.py) | US index quotes from crypto rails: tokenized SPY/QQQ (xStocks) on Kraken (ws v2, `bbo` trigger — the `trades` default gives one update per 8 minutes) and Gate (ws v4 `book_ticker`), Yahoo as best-effort reference, Pyth Hermes optional. Measured feed lag on Gate: 113–116 ms. |
| [`collector/*.sh`](collector/) | Server-side checks: rows per symbol and feed freshness, daily volatility, liquidation cascades, disk growth and "days of space left". |
| [`export_moex.sh`](export_moex.sh), [`export_conductors.sh`](export_conductors.sh) | ClickHouse SQL over the FORTS 2018–2020 archive (QUIK order book, trades): 1-minute bars with wall/imbalance features, aggressor buy/sell volume, touches, intra-minute volatility, last book snapshot. |
| [`fetch_moex_iss.py`](fetch_moex_iss.py), [`fetch_daily.py`](fetch_daily.py), [`fetch_dukascopy.sh`](fetch_dukascopy.sh), [`fetch_us_index.sh`](fetch_us_index.sh) | Free-data fetchers with retries and resumable downloads: MOEX ISS minute and daily candles per contract with a volume-based continuous front (and the ISS SECID-collision quirk documented), Binance/Bybit daily bars + funding history, Dukascopy minute data. |
| [`trading_analyzer/`](trading_analyzer/) | Backtest engine without lookahead, risk module (fixed/ATR stops, trailing, fixed-fractional sizing), maker model with adverse selection, walk-forward, portfolio walk-forward, detector ensemble with regime gates, export to SQL for Grafana. |
| [`event_study.py`](event_study.py), [`conductor_check.py`](conductor_check.py), [`lead_triage.py`](lead_triage.py), [`tail_test.py`](tail_test.py), [`sp_ri_strategy.py`](sp_ri_strategy.py) | The research layer: event studies with thinning, lead-lag triage (β, lagged correlations, multi-leader regression), tail-day multiplier test, trade structure with costs and session filter. |
| [`trend_daily.py`](trend_daily.py), [`funding_scan.py`](funding_scan.py) | Daily-horizon tests: pre-declared trend rule with funding, liquidity screen and spot-long option; extreme-funding episode scanner with capital-constrained simulation. |
| [`grafana/`](grafana/) | Provisioned dashboards: order book walls, price, imbalance. |

Stack: Python 3.9, pandas/numpy only (no heavy dependencies), ClickHouse, Docker Compose, Grafana.

---

## Reproduce the public-data results (no keys needed)

Binance does not serve every country: from a US IP address its API answers HTTP 451, and the Binance downloads below fail. The MOEX block does not touch Binance; the outputs of the funding scan are already in [results/](results/), and the crypto trend tables are in the [trend write-up](posts/2026-09-trend-daily.md) (RU).

```bash
pip install -r requirements.txt

# Daily trend rule on all nine MOEX futures families (MOEX ISS, free);
# on data to 18 Sep 2026 it gave the ensemble 0.83 at t 2.3 (results/trend_daily_*.csv)
python fetch_daily.py --moex && python trend_daily.py --source moex

# The same rule on Binance perps with funding (≈ 5 min download)
python fetch_daily.py --binance && python trend_daily.py --source crypto --exchange binance
python trend_daily.py --source crypto --exchange binance --top 4 --long-spot

# Extreme-funding scan over all Binance USDT perps (≈ 20 min download, then seconds)
python funding_scan.py --fetch --scan --confirm 3
```

The collector needs a server with Docker: `cd collector && docker compose up -d` (set `CLICKHOUSE_PASSWORD`; `SYMBOLS` in `docker-compose.yml`). Deployment helpers read `COLLECTOR_HOST` and `COLLECTOR_KEY` from the environment. Operations runbook (RU): [docs/RUNBOOK.md](docs/RUNBOOK.md).

---

## Write-ups

- [Extreme funding on small perps: the edge that died in 2023 — 658 perpetuals, 1,125 episodes](posts/2026-09-funding-spikes.en.md) ([RU](posts/2026-09-funding-spikes.md)) — also on [dev.to](https://dev.to/stooq1/i-tested-harvest-extreme-funding-on-small-perps-on-all-658-binance-perpetuals-it-died-in-2023-46mh)
- [S&P 500 → RTS futures: what is left of the 2020 conductor effect in 2026](posts/2026-09-conductor-sp500-ri.md) (RU)
- [Trend following on dailies, crypto and FORTS: a crash hedge, not an income](posts/2026-09-trend-daily.md) (RU)
- [I wrote down the pass mark before the test. Then I failed it.](posts/2026-09-prediction-before-the-test.md) (RU) · EN on [dev.to](https://dev.to/stooq1/i-wrote-down-the-pass-mark-before-the-test-then-i-failed-it-4a03)

---

## About

Independent project by [@stooq1](https://github.com/stooq1), June–September 2026. The research questions came from a trader's notebook ("conductors", "walls", "the spring", round levels); the discipline came from getting burned by every one of them in a backtest. Large parts of the code and the journal were written with AI assistants (DeepSeek, Claude); every number in the tables was produced by the scripts in this repository and can be regenerated.

I am open to quant-developer and market-data-engineering work — collectors, ClickHouse pipelines, honest backtest reviews. Open an issue, or write to the Telegram channel [t.me/strategy_graveyard](https://t.me/strategy_graveyard).
