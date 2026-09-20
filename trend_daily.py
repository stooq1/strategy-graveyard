"""Трендовое следование на дневках (time-series momentum) — п.1 плана от 18.09.

Правило объявлено заранее и НЕ оптимизируется:
  сигнал_N(t) = sign(Close_t / Close_{t-N} - 1),  N = 60, 120, 250 баров
  ансамбль(t) = среднее трёх знаков  (в {-1, -1/3, +1/3, +1})
  вес(t)      = ансамбль × (целевая вола / оценка волы_t), |вес| ≤ cap;
                оценка волы — EWMA дневных доходностей, полупериод 20 баров
  сделка      — по Close t, позиция держится до Close t+1; вес пересчитывается
                при смене знака ансамбля или раз в --rebalance баров
  издержки    — cost_bp за сторону на оборот |Δвес|
  фандинг     — перпы: лонг платит, шорт получает (crypto_data/daily/funding_*.csv)
Портфель — равный вес по инструментам, доступным в этот день (после разогрева).

Раскладки по правилам проекта: по годам, по сторонам (лонг/шорт), по
инструментам, топ-5 дней и результат без них, худшие 5 дней, три главные
просадки. Бенчмарк «всегда лонг с той же вол-нормировкой и теми же издержками»
— главный вопрос для крипты: даёт ли тренд что-то сверх простого лонга.

Источники (--source):
  crypto — crypto_data/daily_<биржа>/<SYM>.csv (+ funding_<SYM>.csv), из fetch_daily.py --bybit / --binance;
           биржа задаётся --exchange (по умолчанию bybit — торгуемая площадка)
  moex   — moex_data/daily/cont_<FAM>.csv, из fetch_daily.py --moex
  stooq  — cache/<ticker>_d.pkl (дневки stooq из старого архива, без фандинга)

Запуск:
  .venv/bin/python trend_daily.py --source stooq --symbols btc.v,eth.v,ada.v,sol.v --from 2017-01-01
  .venv/bin/python trend_daily.py --source crypto                      # bybit
  .venv/bin/python trend_daily.py --source crypto --exchange binance
  .venv/bin/python trend_daily.py --source moex
Результат: trend_daily_results.csv (по годам и по инструментам), trend_daily_equity.csv.
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd

# bp за сторону: тейкер (Bybit 5.5, Binance 5) + проскальзывание 2; FORTS — тик + сборы (REVIEW.md 3.7)
COST_BP = {"bybit": 7.5, "binance": 7.0, "stooq": 7.0,
           "Si": 0.3, "Eu": 0.5, "CR": 0.5, "RI": 0.8, "BR": 1.0, "GZ": 1.0, "SR": 1.0,
           "MX": 1.0, "GD": 1.0}
BARS_PER_YEAR = {"crypto": 365, "stooq": 365, "moex": 250}


# ---------------------------------------------------------------- данные

def load(source, symbols, frm, till, exchange="bybit"):
    """{имя: DataFrame[ret, funding, dvol]} — ret дневная простая доходность, funding —
    сумма ставок фандинга за день (0, если данных нет), dvol — дневной оборот в USDT
    (для отбора по ликвидности; NaN, если нет)."""
    out = {}
    if source == "crypto":
        d_in = f"crypto_data/daily_{exchange}"
        files = ([f"{d_in}/{s}.csv" for s in symbols] if symbols
                 else sorted(f for f in glob.glob(f"{d_in}/*.csv") if "funding_" not in f))
        for f in files:
            if not os.path.exists(f):
                print(f"  нет {f}"); continue
            name = os.path.basename(f)[:-4]
            d = pd.read_csv(f, parse_dates=["time"]).set_index("time").sort_index()
            ret = d["close"].pct_change()
            fund = pd.Series(0.0, index=ret.index)
            pf = f"{d_in}/funding_{name}.csv"
            if os.path.exists(pf):
                fr = pd.read_csv(pf, parse_dates=["time"]).set_index("time")["funding_rate"]
                fund = fr.groupby(fr.index.normalize()).sum().reindex(ret.index).fillna(0.0)
            dvol = d["quote_volume"] if "quote_volume" in d.columns else d["close"] * d["volume"]
            out[name] = pd.DataFrame({"ret": ret, "funding": fund, "dvol": dvol})
    elif source == "moex":
        files = ([f"moex_data/daily/cont_{s}.csv" for s in symbols] if symbols
                 else sorted(glob.glob("moex_data/daily/cont_*.csv")))
        for f in files:
            if not os.path.exists(f):
                print(f"  нет {f}"); continue
            name = os.path.basename(f)[5:-4]
            d = pd.read_csv(f, parse_dates=["time"]).set_index("time").sort_index()
            out[name] = pd.DataFrame({"ret": d["ret"], "funding": 0.0, "dvol": d["close"] * d["volume"]})
    elif source == "stooq":
        for s in (symbols or ["btc.v", "eth.v", "ada.v", "sol.v"]):
            f = f"cache/{s}_d.pkl"
            if not os.path.exists(f):
                print(f"  нет {f}"); continue
            d = pd.read_pickle(f).sort_index()
            d.index = pd.to_datetime(d.index)
            out[s] = pd.DataFrame({"ret": d["Close"].pct_change(), "funding": 0.0,
                                   "dvol": d["Close"] * d["Volume"]})
    for k in list(out):
        d = out[k].loc[frm:till]
        d = d[d["ret"].notna()]
        if len(d) < 300:
            print(f"  {k}: меньше 300 дней — пропуск"); del out[k]
        else:
            out[k] = d
    return out


# --------------------------------------------------------------- стратегия

def weights(ret, lookbacks, vol_target, cap, rebalance, bpy, mode):
    """Вес на конец дня t (применяется к доходности t+1). mode: trend | long."""
    px = (1 + ret).cumprod()
    if mode == "trend":
        sig = sum(np.sign(px / px.shift(n) - 1).fillna(0.0) for n in lookbacks) / len(lookbacks)
    else:  # всегда лонг, тот же разогрев — чтобы периоды совпадали
        sig = pd.Series(1.0, index=ret.index)
        sig.iloc[:max(lookbacks)] = 0.0
    vol = ret.ewm(halflife=20, min_periods=20).std() * np.sqrt(bpy)
    target = (sig * vol_target / vol).clip(-cap, cap)
    ready = target.notna() & (sig != 0)
    w = np.zeros(len(ret))
    held, last_sign = 0.0, 0.0
    tv, sv, rv = target.values, np.sign(sig.values), ready.values
    for i in range(len(w)):
        if not rv[i]:
            held, last_sign = 0.0, 0.0
        elif sv[i] != last_sign or i % rebalance == 0:
            held, last_sign = tv[i], sv[i]
        w[i] = held
    return pd.Series(w, index=ret.index), ready


def pnl(ret, funding, w, ready, cost, long_spot=False, spot_cost=None):
    """Дневная доходность стратегии: позиция вчерашняя, издержки на сегодняшний оборот,
    фандинг вчерашней позиции. NaN до разогрева (инструмент не в портфеле).
    long_spot: лонг держится в споте (фандинг не платится, издержки спота),
    шорт — в перпе (фандинг получается)."""
    w_prev = w.shift(1).fillna(0.0)
    gross = w_prev * ret
    per_side = pd.Series(cost, index=w.index)
    if long_spot:
        # сторона сделки: покупка/продажа спота, если позиция после сделки лонг или выходим из лонга
        spot_leg = (w > 0) | ((w == 0) & (w_prev > 0))
        per_side = per_side.where(~spot_leg, spot_cost)
    costs = per_side * (w - w_prev).abs()
    fund = -w_prev * funding
    if long_spot:
        fund = fund.where(w_prev < 0, 0.0)
    r = gross - costs + fund
    r[~ready.shift(1, fill_value=False).astype(bool)] = np.nan
    return pd.DataFrame({"r": r, "gross": gross, "cost": costs, "fund": fund, "w_prev": w_prev})


def liquidity_mask(data, top):
    """Допуск по ликвидности без заглядывания вперёд: в первый день каждого месяца берём
    top инструментов по среднему обороту за прошлые 90 дней; состав держится месяц."""
    dvol = pd.DataFrame({k: d["dvol"] for k, d in data.items()})
    avg = dvol.rolling(90, min_periods=60).mean()
    rank = avg.rank(axis=1, ascending=False)
    elig = (rank <= top).astype(float)
    months = pd.Series(elig.index.to_period("M"), index=elig.index)
    first = (months != months.shift(1)).values
    keep = np.repeat(first[:, None], elig.shape[1], axis=1)
    return elig.where(keep).ffill().fillna(0.0) > 0.5


def stats(r, bpy):
    r = r.dropna()
    if len(r) < 2 or r.std() == 0:
        return dict(days=len(r), ret_pct=np.nan, cagr_pct=np.nan, vol_pct=np.nan, sharpe=np.nan,
                    t=np.nan, maxdd_pct=np.nan)
    eq = (1 + r).cumprod()
    years = len(r) / bpy
    sh = r.mean() / r.std() * np.sqrt(bpy)
    return dict(days=len(r), ret_pct=(eq.iloc[-1] - 1) * 100,
                cagr_pct=(eq.iloc[-1] ** (1 / years) - 1) * 100 if years > 0 else np.nan,
                vol_pct=r.std() * np.sqrt(bpy) * 100, sharpe=sh, t=sh * np.sqrt(years),
                maxdd_pct=(eq / eq.cummax() - 1).min() * 100)


def drawdowns(r, top=3):
    eq = (1 + r.dropna()).cumprod()
    peak = eq.cummax()
    dd = eq / peak - 1
    out, in_dd, start = [], False, None
    for t in dd.index:
        if dd[t] < 0 and not in_dd:
            in_dd, start = True, t
        elif dd[t] == 0 and in_dd:
            seg = dd[start:t]
            out.append((seg.min() * 100, start, seg.idxmin(), t))
            in_dd = False
    if in_dd:
        seg = dd[start:]
        out.append((seg.min() * 100, start, seg.idxmin(), None))
    return sorted(out)[:top]


def fmt_row(name, s):
    return (f"  {name:<22} {s['days']:>5} {s['ret_pct']:>+9.1f} {s['cagr_pct']:>+7.1f} {s['vol_pct']:>6.1f} "
            f"{s['sharpe']:>6.2f} {s['t']:>5.1f} {s['maxdd_pct']:>7.1f}")


HDR = f"  {'вариант':<22} {'дней':>5} {'итого %':>9} {'CAGR %':>7} {'вола %':>6} {'Sharpe':>6} {'t':>5} {'maxDD %':>7}"


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="crypto", choices=["crypto", "moex", "stooq"])
    ap.add_argument("--exchange", default="bybit", choices=["bybit", "binance"], help="для --source crypto")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--from", dest="frm", default="2015-01-01")
    ap.add_argument("--till", default="2030-01-01")
    ap.add_argument("--lookbacks", default="60,120,250")
    ap.add_argument("--vol-target", type=float, default=0.15, help="годовая вола на инструмент")
    ap.add_argument("--cap", type=float, default=2.0, help="потолок |веса| (плечо на инструмент)")
    ap.add_argument("--rebalance", type=int, default=7, help="перевзвешивание раз в N баров")
    ap.add_argument("--cost-bp", type=float, default=None, help="переопределить издержки, bp за сторону")
    ap.add_argument("--no-funding", action="store_true")
    ap.add_argument("--top", type=int, default=0,
                    help="допуск по ликвидности: top-N по обороту за 90 дней, состав раз в месяц (0 = все)")
    ap.add_argument("--long-spot", action="store_true",
                    help="лонг держать в споте (без фандинга, издержки --spot-cost-bp), шорт в перпе")
    ap.add_argument("--spot-cost-bp", type=float, default=12.0, help="тейкер спота 10 + проскальзывание 2")
    a = ap.parse_args()
    lookbacks = [int(x) for x in a.lookbacks.split(",")]
    symbols = [s.strip() for s in a.symbols.split(",") if s.strip()]
    bpy = BARS_PER_YEAR[a.source]
    data = load(a.source, symbols, a.frm, a.till, a.exchange)
    if not data:
        print("нет данных"); return

    src = f"{a.source}/{a.exchange}" if a.source == "crypto" else a.source
    print(f"Данные ({src}), баров в году {bpy}, окна {lookbacks}, вол-таргет {a.vol_target:.0%}, "
          f"cap {a.cap}, ребаланс {a.rebalance} баров"
          + (f", допуск top-{a.top} по обороту" if a.top else "")
          + (f", лонг в споте ({a.spot_cost_bp:.0f} bp/сторона)" if a.long_spot else ""))
    elig = liquidity_mask(data, a.top) if a.top else None
    costs = {}
    for k, d in data.items():
        cost_key = k if a.source == "moex" else (a.exchange if a.source == "crypto" else a.source)
        c = a.cost_bp if a.cost_bp is not None else COST_BP.get(cost_key, 1.0)
        costs[k] = c / 1e4
        f_ann = d["funding"].mean() * bpy * 100
        print(f"  {k:<10} {d.index[0].date()} – {d.index[-1].date()}  дней {len(d):>5}  "
              f"вола {d['ret'].std() * np.sqrt(bpy) * 100:5.0f}%  издержки {c:.1f} bp/сторона"
              + (f"  фандинг {f_ann:+.1f}%/год" if d["funding"].abs().sum() > 0 else ""))
    if a.no_funding:
        for d in data.values():
            d["funding"] = 0.0

    variants = [("ансамбль 60/120/250", lookbacks, "trend")] + \
               [(f"один N={n}", [n], "trend") for n in lookbacks] + \
               [("всегда лонг (вол-норм.)", lookbacks, "long")]
    port, detail = {}, {}
    for name, lb, mode in variants:
        per = {}
        for k, d in data.items():
            w, ready = weights(d["ret"], lookbacks if mode == "long" else lb, a.vol_target, a.cap,
                               a.rebalance, bpy, mode)
            if elig is not None:
                ok = elig[k].reindex(w.index, fill_value=False).astype(bool)
                w = w.where(ok, 0.0)
                ready = ready & ok
            per[k] = pnl(d["ret"], d["funding"], w, ready, costs[k], a.long_spot, a.spot_cost_bp / 1e4)
        rets = pd.DataFrame({k: v["r"] for k, v in per.items()})
        port[name] = rets.mean(axis=1, skipna=True)
        detail[name] = per
    # buy&hold равными долями, тот же разогрев
    bh = pd.DataFrame({k: d["ret"].where(detail["всегда лонг (вол-норм.)"][k]["r"].notna())
                       for k, d in data.items()})
    port["B&H равные доли"] = bh.mean(axis=1, skipna=True)

    print("\nПортфель, все годы:")
    print(HDR)
    for name, r in port.items():
        print(fmt_row(name, stats(r, bpy)))

    main = "ансамбль 60/120/250"
    bench = "всегда лонг (вол-норм.)"
    r_main, r_bench = port[main], port[bench]
    n_inst = pd.DataFrame({k: v["r"] for k, v in detail[main].items()}).notna().sum(axis=1)
    rows = []
    print(f"\nПо годам — {main} против «{bench}»:")
    print(f"  {'год':<5} {'инстр':>5} | {'тренд %':>8} {'Sharpe':>6} {'maxDD':>6} {'лонг дн':>7} {'шорт дн':>7} | "
          f"{'лонг %':>8} {'Sharpe':>6} {'maxDD':>6}")
    for y, r in r_main.dropna().groupby(r_main.dropna().index.year):
        s, sb = stats(r, bpy), stats(r_bench.reindex(r.index), bpy)
        wp = pd.DataFrame({k: v["w_prev"].reindex(r.index) for k, v in detail[main].items()}).fillna(0.0)
        long_share = (wp > 0).sum().sum() / max(1, (wp != 0).sum().sum()) * 100
        print(f"  {y:<5} {n_inst.loc[r.index].mean():>5.1f} | {s['ret_pct']:>+8.1f} {s['sharpe']:>6.2f} "
              f"{s['maxdd_pct']:>6.1f} {long_share:>6.0f}% {100 - long_share:>6.0f}% | "
              f"{sb['ret_pct']:>+8.1f} {sb['sharpe']:>6.2f} {sb['maxdd_pct']:>6.1f}")
        rows.append(dict(kind="year", key=y, n_inst=n_inst.loc[r.index].mean(), **s,
                         bench_ret_pct=sb["ret_pct"], bench_sharpe=sb["sharpe"]))

    print(f"\nПо инструментам — {main} (суммы дневных доходностей, % за весь период):")
    print(f"  {'инстр':<10} {'дней':>5} {'Sharpe':>6} {'итого %':>8} {'лонг %':>8} {'шорт %':>8} "
          f"{'издерж %':>8} {'фандинг %':>9} {'смен знака':>10} {'лонг-бенч Sharpe':>16}")
    for k, v in detail[main].items():
        r = v["r"].dropna()
        s = stats(r, bpy)
        g = v["gross"].loc[r.index]
        wp = v["w_prev"].loc[r.index]
        flips = int((np.sign(wp) != np.sign(wp.shift(1))).sum())
        sb = stats(detail[bench][k]["r"], bpy)
        print(f"  {k:<10} {s['days']:>5} {s['sharpe']:>6.2f} {r.sum() * 100:>+8.1f} {g[wp > 0].sum() * 100:>+8.1f} "
              f"{g[wp < 0].sum() * 100:>+8.1f} {-v['cost'].loc[r.index].sum() * 100:>8.1f} "
              f"{v['fund'].loc[r.index].sum() * 100:>+9.1f} {flips:>10} {sb['sharpe']:>16.2f}")
        rows.append(dict(kind="instrument", key=k, **s, long_pct=g[wp > 0].sum() * 100,
                         short_pct=g[wp < 0].sum() * 100, cost_pct=-v["cost"].loc[r.index].sum() * 100,
                         funding_pct=v["fund"].loc[r.index].sum() * 100, flips=flips, bench_sharpe=sb["sharpe"]))

    r = r_main.dropna()
    total = r.sum()
    top5, worst5 = r.nlargest(5), r.nsmallest(5)
    s_wo = stats(r.drop(top5.index), bpy)
    print(f"\nКонцентрация — {main}:")
    print(f"  сумма дневных доходностей {total * 100:+.1f}%; топ-5 дней {top5.sum() * 100:+.1f}% "
          f"({top5.sum() / total * 100 if total else float('nan'):.0f}% суммы); без них Sharpe {s_wo['sharpe']:.2f}, "
          f"CAGR {s_wo['cagr_pct']:+.1f}%")
    print("  топ-5:   " + ", ".join(f"{t.date()} {v * 100:+.1f}%" for t, v in top5.items()))
    print("  худшие:  " + ", ".join(f"{t.date()} {v * 100:+.1f}%" for t, v in worst5.items()))
    print("  просадки: " + "; ".join(
        f"{d:.1f}% {s.date()}→{tr.date()}" + (f" (выход {e.date()})" if e is not None else " (не вышли)")
        for d, s, tr, e in drawdowns(r)))
    corr = r.corr(r_bench.reindex(r.index))
    print(f"  корреляция с «{bench}» {corr:+.2f}")

    pd.DataFrame(rows).to_csv("trend_daily_results.csv", index=False)
    eq = pd.DataFrame({k: (1 + v.fillna(0)).cumprod() for k, v in port.items()})
    eq.to_csv("trend_daily_equity.csv")
    print("\nЗаписано: trend_daily_results.csv, trend_daily_equity.csv")


if __name__ == "__main__":
    main()
