"""Event study: условная доходность ПОСЛЕ события, по горизонтам.

Слой между «идеей» и «бэктестом». Бэктест смешивает в одном числе сигнал,
горизонт удержания, стопы, комиссию и подбор порогов — минус в нём
неоднозначен (нет сигнала? не тот горизонт? съела комиссия?). Event study
отвечает на один вопрос: есть ли после события информация о знаке цены,
на каком горизонте и сколько её в bp — до всяких стопов и порогов.
Сравнивать результат надо с издержками: 1 тик (печатается) + комиссия.

Исполнение как в движке: вход по Open следующего бара, выход по Close через
h баров. События с пересекающимися окнами прореживаются (не чаще одного на
h+1 баров), чтобы подряд идущие минуты одного эпизода не считались как
независимые наблюдения и не завышали t-статистику.

Знак: bp считаются «в сторону гипотезы» — положительное число значит, что
после события цена шла туда, куда предсказывает гипотеза.

Источники событий:
  --detectors      голоса wall_bounce / wall_pull / imbalance (|score| > 0.5)
  --patterns       ценовые паттерны из исходных заметок (только OHLCV):
                   break_*  — «стояние на месте и прорыв»: сжатие 15-мин.
                              диапазона + выход за него на объёме ≥2× медианы,
                              гипотеза: продолжение в сторону прорыва;
                   fail_*   — «штурмует-срывает-возвращается» (ложный пробой):
                              новый 60-мин. экстремум за последние 5 мин, но
                              Close уже вернулся за прежний уровень; гипотеза:
                              ход обратно.
  --flow           события по потоку агрессоров и тикам (нужны flow_*.csv из
                   export_conductors.sh / fetch_crypto_flow.sh):
                   ofi_*         — дисбаланс агрессоров (taker_buy − taker_sell)
                                   /(сумма), z > 2 за сутки; гипотеза: продолжение;
                   assault_*     — «>10 касаний за минуту И самая большая
                                   секундная волатильность за 10 мин» (из заметок);
                                   гипотеза: пробой в сторону касаний;
                   eaten_*       — плита исчезла к концу минуты, и у её цены
                                   прошёл объём ≥ половины плиты («съели»);
                                   гипотеза: пробой в сторону плиты;
                   pulled_*      — плита исчезла без объёма («сняли», спуфинг);
                                   гипотеза: пробой в сторону плиты (по заметкам
                                   «срывает»);
                   liq_*         — (крипта) всплеск ликвидаций лонгов/шортов,
                                   z > 3; гипотеза: отскок против каскада.
  --leader SYM     события по «проводнику» (родительскому инструменту):
                   lead_*   — большой ход лидера за минуту (|r| > 2σ),
                              гипотеза: наш пойдёт следом;
                   spring_* — «пружина»: остаток нашего хода к бете лидера
                              за --spring-win минут, |z| > 2; гипотеза:
                              отставший догонит, убежавший вернётся.
Разрезы: режим волатильности (hot = 60-мин. вола > 1.3× медианы за день),
час суток (UTC) на горизонте --hour-h.

Файлы: <symbol>.csv, flow_<symbol>.csv (с потоком) и inv_<code>.csv
(котировки investing.com: time, close) в crypto_data/ или moex_data/.
Символ задаётся именем файла без .csv: RIH0, flow_RIH0, inv_S_P_500.

Запуск:
  python event_study.py --source crypto --symbols BTCUSDT,ATOMUSDT --detectors
  python event_study.py --source crypto --symbols SOLUSDT,ATOMUSDT --leader BTCUSDT
  python event_study.py --source moex --symbols RIH0,RIM0,RIU0 --leader SiH0 --patterns
  python event_study.py --source moex --symbols flow_RIH0 --flow --leader inv_S_P_500
Результат: консоль + event_study_results.csv
"""
import argparse
import os
import warnings

import numpy as np
import pandas as pd

from trading_analyzer.detectors import (
    WallBounceDetector, WallPullDetector, ImbalanceDetector,
)

warnings.filterwarnings("ignore")

OHLCV = {"open": "Open", "high": "High", "low": "Low", "close": "Close",
         "volume": "Volume"}
# фронт-периоды контрактов FORTS (как в run_walls.py)
FRONT = {
    "SiH0": ("2020-02-25", "2020-03-19"), "SiM0": ("2020-03-20", "2020-06-18"),
    "SiU0": ("2020-06-19", "2020-09-03"), "RIH0": ("2020-02-25", "2020-03-19"),
    "RIM0": ("2020-03-20", "2020-06-18"), "RIU0": ("2020-06-19", "2020-09-03"),
}
DAY_BARS = {"crypto": 1440, "moex": 840}   # баров в сутках
FLOW_COLS = ["taker_buy", "taker_sell", "touch_high", "touch_low",
             "sec_std_bp", "vol_at_high", "vol_at_low"]


def load(source: str, sym: str) -> pd.DataFrame:
    """sym — имя файла без .csv: RIH0, flow_RIH0, inv_S_P_500."""
    folder = "crypto_data" if source == "crypto" else "moex_data"
    path = f"{folder}/{sym}.csv"
    if not os.path.exists(path):
        raise FileNotFoundError(f"нет {path}")
    df = pd.read_csv(path, parse_dates=["time"])
    df = df.set_index("time").sort_index().rename(columns=OHLCV)
    if "Open" not in df:                       # inv_*.csv: только close
        for c in ("Open", "High", "Low"):
            df[c] = df["Close"]
        if "Volume" not in df:
            df["Volume"] = df.get("n_ticks", 0)
    code = sym.replace("flow_", "")
    if source == "moex" and code in FRONT:
        df = df.loc[FRONT[code][0]:FRONT[code][1]]
    return df


def fwd_bp(df: pd.DataFrame, h: int) -> pd.Series:
    """Доходность от Open следующего бара до Close через h баров, bp."""
    return (df["Close"].shift(-1 - h) / df["Open"].shift(-1) - 1) * 1e4


def thin(mask: pd.Series, gap: int) -> pd.Series:
    """Оставить события не чаще одного на gap баров (первое в кластере)."""
    idx = np.flatnonzero(mask.values)
    keep, last = [], -10 ** 9
    for i in idx:
        if i - last >= gap:
            keep.append(i)
            last = i
    out = pd.Series(False, index=mask.index)
    out.iloc[keep] = True
    return out


def stat(x: pd.Series):
    x = x.dropna()
    n = len(x)
    if n < 3 or x.std() == 0:
        return np.nan, np.nan, n
    return float(x.mean()), float(x.mean() / (x.std() / np.sqrt(n))), n


def tick_bp(df: pd.DataFrame) -> float:
    d = df["Close"].diff().abs()
    tick = d[d > 0].min()
    return float(tick / df["Close"].median() * 1e4)


def regime_mask(df: pd.DataFrame, day_bars: int) -> pd.Series:
    r = df["Close"].pct_change()
    vol = r.rolling(60, min_periods=30).std()
    ref = vol.rolling(day_bars, min_periods=day_bars // 4).median()
    return (vol > 1.3 * ref).fillna(False)


def zscore(x: pd.Series, window: int) -> pd.Series:
    mean = x.rolling(window, min_periods=window // 4).mean()
    std = x.rolling(window, min_periods=window // 4).std()
    return (x - mean) / std.replace(0, np.nan)


def detector_events(df: pd.DataFrame):
    """{имя_события: (маска, направление ±1)} из голосов детекторов."""
    out = {}
    for d in (WallBounceDetector(), WallPullDetector(), ImbalanceDetector()):
        s = d.score(df)
        out[f"{d.name}+"] = ((s > 0.5), +1)
        out[f"{d.name}-"] = ((s < -0.5), -1)
    return out


def pattern_events(df: pd.DataFrame, day_bars: int, win: int = 15):
    """Паттерны из исходных заметок, считаемые по OHLCV."""
    hi = df["High"].rolling(win).max().shift(1)     # диапазон ПРЕДЫДУЩИХ win баров
    lo = df["Low"].rolling(win).min().shift(1)
    rng = (hi - lo) / df["Close"]
    tight = rng < rng.rolling(day_bars, min_periods=day_bars // 4).quantile(0.3)
    vol_spike = df["Volume"] > 2 * df["Volume"].rolling(
        day_bars, min_periods=day_bars // 4).median()
    break_up = (tight & (df["Close"] > hi) & vol_spike).fillna(False)
    break_dn = (tight & (df["Close"] < lo) & vol_spike).fillna(False)

    hi60 = df["High"].rolling(60).max().shift(5)     # уровень до штурма
    lo60 = df["Low"].rolling(60).min().shift(5)
    assault_up = df["High"].rolling(5).max() > hi60  # за 5 мин пробили вверх
    assault_dn = df["Low"].rolling(5).min() < lo60
    fail_up = (assault_up & (df["Close"] < hi60)).fillna(False)   # и вернулись
    fail_dn = (assault_dn & (df["Close"] > lo60)).fillna(False)
    return {
        "break_up": (break_up, +1), "break_dn": (break_dn, -1),
        "fail_up": (fail_up, -1), "fail_dn": (fail_dn, +1),
    }


def flow_events(df: pd.DataFrame, day_bars: int):
    """События по потоку агрессоров, касаниям и плитам (flow_*.csv)."""
    missing = [c for c in FLOW_COLS if c not in df]
    if missing:
        raise ValueError(f"--flow требует flow_*.csv, нет колонок {missing}")
    out = {}
    total = (df["taker_buy"] + df["taker_sell"]).replace(0, np.nan)
    ofi = ((df["taker_buy"] - df["taker_sell"]) / total).fillna(0.0)
    z = zscore(ofi, day_bars)
    out["ofi_up"] = ((z > 2).fillna(False), +1)
    out["ofi_dn"] = ((z < -2).fillna(False), -1)

    # «>10 касаний за последнюю минуту И самая большая волатильность
    # в секунде за 10 мин» — определение из заметок, буквально
    top_vol = df["sec_std_bp"] >= df["sec_std_bp"].rolling(10, min_periods=5).max()
    out["assault_up"] = (((df["touch_high"] >= 10) & top_vol).fillna(False), +1)
    out["assault_dn"] = (((df["touch_low"] >= 10) & top_vol).fillna(False), -1)

    # плита исчезла к концу минуты: съели (объём у экстремума >= 1/2 плиты)
    # или сняли (объём < 1/10 плиты)
    if "last_offer_wall_qty" in df:
        for side, sign, wall, last, vol_at in (
                ("up", +1, "offer_wall_qty", "last_offer_wall_qty", "vol_at_high"),
                ("dn", -1, "bid_wall_qty", "last_bid_wall_qty", "vol_at_low")):
            w = df[wall].replace(0, np.nan)
            big = w > 3 * w.rolling(600, min_periods=60).median()
            gone = df[last].fillna(0) < 0.3 * w
            eaten = (big & gone & (df[vol_at] >= 0.5 * w)).fillna(False)
            pulled = (big & gone & (df[vol_at] < 0.1 * w)).fillna(False)
            out[f"eaten_{side}"] = (eaten, sign)
            out[f"pulled_{side}"] = (pulled, sign)

    if "liq_long" in df:   # крипта: каскад ликвидаций -> ставка на отскок
        zl = zscore(df["liq_long"].fillna(0), day_bars)
        zs = zscore(df["liq_short"].fillna(0), day_bars)
        out["liq_long_spike"] = ((zl > 3).fillna(False), +1)
        out["liq_short_spike"] = ((zs > 3).fillna(False), -1)
    return out


def leader_events(df: pd.DataFrame, leader: pd.Series, day_bars: int,
                  spring_win: int):
    lead = leader.reindex(df.index).ffill()
    r_l = lead.pct_change()
    r_f = df["Close"].pct_change()
    print("  кросс-корреляция corr(наш[t], лидер[t-lag]), lag>0 = лидер впереди:",
          {lag: round(float(r_f.corr(r_l.shift(lag))), 3)
           for lag in (-2, -1, 0, 1, 2)})
    # σ лидера по минутам, где он реально обновлялся: ETF/индексы вне торгов
    # стоят (ретурн 0), и нули занижали бы порог k·σ
    sd = r_l.where(r_l != 0).rolling(day_bars, min_periods=day_bars // 8).std()
    beta = (r_f.rolling(day_bars, min_periods=day_bars // 4).cov(r_l)
            / r_l.rolling(day_bars, min_periods=day_bars // 4).var())
    sign = float(np.sign(beta.median()))  # знак связи (RI–Si < 0)
    resid = (df["Close"].pct_change(spring_win)
             - beta * lead.pct_change(spring_win))
    z = resid / resid.rolling(day_bars, min_periods=day_bars // 4).std()
    print(f"  медианная бета к лидеру {beta.median():+.3f}, "
          f"событий пружины |z|>2: {int((z.abs() > 2).sum())}")
    return {
        "lead_up": ((r_l > 2 * sd).fillna(False), +1 * sign),
        "lead_dn": ((r_l < -2 * sd).fillna(False), -1 * sign),
        "spring_lagged": ((z < -2).fillna(False), +1),   # отстал -> догонит
        "spring_ahead": ((z > 2).fillna(False), -1),     # убежал -> вернётся
    }


def run(source, sym, df, events, horizons, hour_h, day_bars, rows):
    hot = regime_mask(df, day_bars)
    fw = {h: fwd_bp(df, h) for h in horizons}
    print(f"  1 тик = {tick_bp(df):.2f} bp; безусловная средняя, bp: "
          + ", ".join(f"h{h} {fw[h].mean():+.2f}" for h in horizons))
    head = f"  {'событие':<16}" + "".join(f"{'h' + str(h):>22}" for h in horizons)
    print(head)
    for name, (mask, direction) in events.items():
        line = f"  {name:<16}"
        for h in horizons:
            m = thin(mask, h + 1)
            x = fw[h][m] * direction
            mean, t, n = stat(x)
            mh, th, nh = stat(x[hot[m]])
            mc, tc, nc = stat(x[~hot[m]])
            line += (f"{mean:+7.1f}bp t{t:+5.1f} n{n:<5d}"
                     if n >= 10 else f"{'n=' + str(n):>22}")
            rows.append({"source": source, "symbol": sym, "event": name,
                         "h": h, "n": n, "mean_bp": mean, "t": t,
                         "uncond_bp": fw[h].mean() * direction,
                         "hot_bp": mh, "hot_t": th, "hot_n": nh,
                         "calm_bp": mc, "calm_t": tc, "calm_n": nc})
        print(line)
    # режим и час суток на одном горизонте
    print(f"  --- разрез на h={hour_h}: hot / calm, и по часам (UTC, n>=15)")
    for name, (mask, direction) in events.items():
        m = thin(mask, hour_h + 1)
        x = fw[hour_h][m] * direction
        mh, th, nh = stat(x[hot[m]])
        mc, tc, nc = stat(x[~hot[m]])
        byh = x.groupby(x.index.hour).agg(["mean", "count"])
        hours = {int(k): round(float(v["mean"]), 1) for k, v in byh.iterrows()
                 if v["count"] >= 15}
        print(f"  {name:<16} hot {mh:+6.1f}bp t{th:+4.1f} n{nh:<4d} | "
              f"calm {mc:+6.1f}bp t{tc:+4.1f} n{nc:<4d} | по часам {hours}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["crypto", "moex"], default="crypto")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,ATOMUSDT")
    ap.add_argument("--detectors", action="store_true",
                    help="события из голосов детекторов стакана")
    ap.add_argument("--patterns", action="store_true",
                    help="ценовые паттерны: прорыв из сжатия, ложный пробой")
    ap.add_argument("--flow", action="store_true",
                    help="поток агрессоров, касания, съели/сняли плиту (flow_*.csv)")
    ap.add_argument("--leader", default=None,
                    help="символ-проводник (файл без .csv): lead-lag и пружина")
    ap.add_argument("--spring-win", type=int, default=15)
    ap.add_argument("--horizons", default="1,2,5,15,60")
    ap.add_argument("--hour-h", type=int, default=15)
    args = ap.parse_args()
    if not (args.detectors or args.leader or args.patterns or args.flow):
        args.detectors = True

    horizons = [int(h) for h in args.horizons.split(",")]
    day_bars = DAY_BARS[args.source]
    leader = load(args.source, args.leader)["Close"] if args.leader else None
    rows = []
    for sym in args.symbols.split(","):
        df = load(args.source, sym)
        print(f"\n=== {sym} [{df.index[0]} – {df.index[-1]}] {len(df)} баров"
              + (f", проводник {args.leader}" if args.leader else ""))
        events = {}
        if args.detectors:
            events.update(detector_events(df))
        if args.patterns:
            events.update(pattern_events(df, day_bars))
        if args.flow:
            events.update(flow_events(df, day_bars))
        if leader is not None:
            events.update(leader_events(df, leader, day_bars, args.spring_win))
        run(args.source, sym, df, events, horizons, args.hour_h, day_bars, rows)

    pd.DataFrame(rows).to_csv("event_study_results.csv", index=False)
    print("\nСохранено: event_study_results.csv")


if __name__ == "__main__":
    main()
