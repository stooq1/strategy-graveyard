# Extreme funding on small perps: the edge that died in 2023

*658 Binance perpetuals, 364 hedgeable, 1,125 episodes, one pre-declared rule. Code and data: [funding_scan.py](../funding_scan.py), [results/funding_scan_episodes.csv](../results/funding_scan_episodes.csv).*

## The promise

There is an idea people like to recommend to traders with small accounts: harvest extreme funding. A perpetual on a hyped coin trades at a premium to spot, leveraged longs pay shorts 0.5–2% every eight hours — hundreds, sometimes thousands of percent annualized. Short the perp, buy the same amount of spot, price risk is hedged, sit and collect. Funds cannot go there: a small perp has no capacity even for a million dollars. For \$10,000 it has plenty. It sounds like the one class of edge where small capital is an advantage.

I tested it on the complete Binance USDT-M funding history, September 2019 to September 2026. Below: the rule, the numbers, and why this edge does not exist in 2025–2026.

## What exactly was tested

**Data.** The current list of Binance USDT perpetuals from `exchangeInfo` — 658 symbols. 364 of them have a USDT spot pair on the same exchange: only those can be hedged and only those enter the calculation. Every symbol's full funding history (`/fapi/v1/fundingRate`), 2019-09-10 to 2026-09-18. One caveat matters: `exchangeInfo` only lists live contracts. Delisted perps — exactly the ones where things went worst — are not in the sample, so reality is worse than the numbers below, not better.

**The rule** was written down before the run and never tuned:

- entry after three consecutive payments at ≥ 100% annualized (converted using the actual payment interval — 8, 4 or 1 hour);
- position: short perp + long spot of equal size; each subsequent positive payment is collected, each negative one is paid;
- exit after the first payment below 30% annualized;
- costs: 50 bp of notional per episode — two taker round trips (spot 10 bp + perp 5 bp per side) plus a slippage allowance for illiquid names;
- basis ignored. This is conservative: when funding is extreme the perp trades at a premium, the short is opened rich and closed after the premium collapses — a gain, not a loss.

The "three consecutive payments" filter is not optimization but protection against false starts: a single payment at 100% annualized on an 8-hour interval is 0.09% of notional, less than the episode's costs. The single-payment variant is shown too; it is worse everywhere.

**Two output numbers.** An upper bound — "take every episode at \$10,000" (nobody has that capital, but it shows the size of the phenomenon). And a simulation: \$10,000, at most three concurrent positions of \$3,333, episodes taken in time order, skipped when all slots are busy.

## Results

1,125 episodes in total, about 160 per year. Median duration 2.7 days. After costs 58% are profitable, median episode +0.20% of notional, mean +1.02%. The top-5 episodes are only 6% of the total — this is not a story about five lucky trades. By year:

| year | episodes | profitable | median net, % notional | sum net, % notional | "take all at \$10k", USD | simulation \$10k / 3 slots, USD |
|---|---|---|---|---|---|---|
| 2020 | 170 | 52% | +0.1 | +66 | +6,623 | +597 |
| 2021 | 615 | 63% | +0.6 | **+1,001** | +100,103 | +2,750 |
| 2022 | 0 | — | — | — | 0 | 0 |
| 2023 | 37 | 22% | −0.2 | −5 | −513 | +26 |
| 2024 | 233 | 68% | +0.3 | +105 | +10,493 | +284 |
| 2025 | 43 | 21% | −0.3 | −8 | −773 | −258 |
| 2026 (to Sep 18) | 27 | **4%** | −0.4 | −9 | −850 | −283 |

In 2021 it worked, and how: IOTA +15.3% of notional in 20 days (61 consecutive payments at 104% annualized and above), TRB +14.9%, ANKR +14.0%, EGLD +12.7%, YFI +12.6%, 1INCH +12.0% — all in January–February and March–April 2021. January 2021 alone, summed across all hedgeable perps, would have paid more than \$100,000 per \$10,000-per-episode, if you had infinitely many such \$10,000s.

From 2022 through 2026 the total is −\$2,136 on the upper bound and −\$515 in the simulation. The last twelve months: −\$293.

## Why it died

Three things are visible directly in the data.

**Payment interval.** In the 2020–2021 episodes the median interval between payments is 8 hours. From 2023 it is 4 hours: Binance moved volatile contracts to more frequent funding. The same annualized rate is now paid in half-size portions, and the "three consecutive payments" rule triggers after 12 hours instead of a day — faster, one would think. But:

**Episode duration.** The median episode lasted 3.0 days in 2021 and in 2024, 0.67 days in 2025 and 0.5 days in 2026. The spike collapses within one or two payments after it becomes visible. Somebody harvests it faster than it can be confirmed to exist — and that somebody is not a person with \$10,000 but bots for which the predicted next funding rate is a signal measured in seconds.

**New listings.** A separate check over 361 listings: the average funding in the first 14 days is **−53% annualized**. Median +5%; above 100% in five percent of listings. New coins get shorted by the crowd, and the crowd pays the longs, not the other way round. "Harvesting funding on listings" in 2024–2026 means paying it.

## Maybe the rule is just slow?

I checked an optimistic bound: entry on the *predicted* rate, i.e. one payment earlier than my rule allows, with the entry payment credited. This is better than anything actually executable. Sum of net across all episodes of the year, % of notional:

| entry threshold | 2021 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| 100% annualized, 3 payments | +1,108 | +123 | −4.0 | −6.6 |
| 200% annualized, 3 payments | +663 | +11 | +4.6 | −1.4 |
| 300% annualized, 2 payments | +351 | +3.5 | +5.3 | −2.2 |

So even taking *every* episode in the market in 2025, ahead of the rule and with unlimited capital, you would have collected 4–5% of notional over the year — \$500 on \$10,000 for infinite attention. In 2026, a loss at any threshold.

As a control, the 16 majors alone (BTC, ETH, XRP, ADA, LTC…): episodes at ≥ 100% annualized exist only in 2020–2021 and in a short burst in 2024; 2022–2023 and 2025–2026 have none. The \$10,000 simulation on majors: +\$364 per year on average, all of it from 2021.

## What follows

For a small-capital trader in 2026, the "capacity edge on small perps" story is a story. The phenomenon was real, large and broad in 2021 (not five coins but hundreds of episodes, 63% of them profitable), came back for a few months in 2024, and has been negative under every reading of the data since. The cause is structural — more frequent funding and automation — and structural causes do not revert on their own.

The general lesson is the same as in the rest of this project: before believing in an edge, measure it on the full sample, with a rule written down in advance, with costs, and with a simulation of the capital you actually have. Here that took an hour of scripting and twenty minutes of downloading.

Reproduce:

```bash
python funding_scan.py --fetch --scan --confirm 3
python funding_scan.py --scan --enter 200 --confirm 3     # threshold sensitivity
```

All episodes with dates, durations and results: [results/funding_scan_episodes.csv](../results/funding_scan_episodes.csv); the yearly summary: [results/funding_scan_by_year.csv](../results/funding_scan_by_year.csv).

*Not modelled, and making reality worse: delisted perps (absent from the sample), delisting risk on an open position, inability to close spot in a thin book, basis moving against the position when exiting before the premium collapses, margin on the short leg during vertical rallies. Not modelled, and making it better: the perp premium at entry (it accrues to the short when it collapses). The second is smaller than the first.*
