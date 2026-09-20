"""Трейд-структура вокруг найденного эффекта S&P 500 -> RI/Si (REVIEW.md §6).

Не новый поиск параметров: k, h, направление (только вверх) и издержки за
круг берутся как уже установлено в event-study (REVIEW.md §6.1), это
проверка конкретной торговой структуры на этих цифрах, а не подбор новых.

Правила: событие -- S&P прошёл > k*sigma за минуту (sigma по активным
минутам лидера, скользяще за сутки); вход по Open(t+1), выход по
Close(t+1+h); только лонг (эффект живёт только на ходе S&P вверх).
Издержки -- фиксированный bp за круг, вычитается из каждой сделки.

Добавляет предиктивный (не реактивный) режимный фильтр: торговать только
если реализованная вола ПРЕДЫДУЩЕГО торгового дня выше исторической
медианы (расширяющееся окно -- только прошлое, без заглядывания вперёд).
Сравнивает "с фильтром" / "без фильтра" по периодам H0/M0/U0 и по k=2/3.

Запуск: .venv/bin/python sp_ri_strategy.py --target flow_RI --leader inv_SP500
"""
import argparse
import warnings

import numpy as np
import pandas as pd

from event_study import load, fwd_bp, thin, stat

warnings.filterwarnings("ignore")

PERIODS = {"H0 (25.02-19.03)": ("2020-02-25", "2020-03-19"),
           "M0 (20.03-18.06)": ("2020-03-20", "2020-06-18"),
           "U0 (19.06-03.09)": ("2020-06-19", "2020-09-03")}
COST_BP = {"flow_RI": 1.5, "flow_Si": 0.5}
DAY_BARS = 840


def prior_day_vol_gate(df, day_bars=DAY_BARS, warmup_days=3):
    """True, если вола ПРЕДЫДУЩЕГО дня выше исторической (только прошлой)
    медианы. daily_vol в момент t = std 1-мин. доходностей за последние
    day_bars баров; prior_vol = это же значение на конец предыдущего дня
    (сдвиг на day_bars баров вперёд от точки расчёта); порог -- expanding
    median самого prior_vol до текущей точки (без будущего). warmup_days
    мало (3), а не условные 10: выборка стартует 25.02.2020, за 10 дней
    разгона фильтр съедает почти весь паникующий H0 (самые вола-дни марта)
    просто как "нет ещё истории" -- это артефакт окна, а не сигнал."""
    r = df["Close"].pct_change()
    daily_vol = r.rolling(day_bars, min_periods=day_bars // 4).std()
    prior_vol = daily_vol.shift(day_bars)
    thr = prior_vol.expanding(min_periods=day_bars * warmup_days).median()
    gate = (prior_vol > thr).fillna(False)
    eligible = thr.notna()
    return gate, eligible


def max_dd_bp(cum: pd.Series) -> float:
    if not len(cum):
        return 0.0
    return float((cum - cum.cummax()).min())


def run_one(target, leader, k, h):
    df = load("moex", target)
    lead = load("moex", leader)["Close"].reindex(df.index).ffill()
    r_l, r_t = lead.pct_change(), df["Close"].pct_change()
    sd = r_l.where(r_l != 0).rolling(DAY_BARS, min_periods=DAY_BARS // 8).std()
    beta = (r_t.rolling(DAY_BARS, min_periods=DAY_BARS // 4).cov(r_l)
            / r_l.rolling(DAY_BARS, min_periods=DAY_BARS // 4).var())
    direction = float(np.sign(beta.median()))   # Si (USD/RUB) идёт ПРОТИВ S&P:
                                                 # лонг S&P -> шорт Si, не лонг
    ev = thin((r_l > k * sd).fillna(False), h + 1)
    fw = fwd_bp(df, h) * direction              # bp сделки в СВОЮ сторону (шорт => знак фьючерса перевёрнут)
    cost = COST_BP.get(target, 1.0)
    gate, eligible = prior_day_vol_gate(df)

    rows = []
    cov = {}
    for name, bounds in list(PERIODS.items()) + [("ВСЕ", (None, None))]:
        if bounds[0]:
            in_period = (df.index >= bounds[0]) & (df.index <= bounds[1])
        else:
            in_period = pd.Series(True, index=df.index)
        sel = ev & in_period
        n_ev = int(sel.sum())
        n_elig = int((sel & eligible).sum())
        cov[name] = (n_ev, n_elig)
        for mask, label in ((sel, "без фильтра"), (sel & gate, "с фильтром")):
            x = fw[mask].dropna()
            mean, t, n = stat(x)
            net = x - cost
            net_mean, net_t, _ = stat(net)
            cum = net.cumsum()
            dd = max_dd_bp(cum)
            hit = float((x > 0).mean()) * 100 if n else float("nan")
            total = float(cum.iloc[-1]) if len(cum) else 0.0
            rows.append((name, label, n, mean, net_mean, net_t, hit, total, dd))
    return rows, cost, df.index.min(), df.index.max(), int(gate.sum()), cov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="flow_RI")
    ap.add_argument("--leader", default="inv_SP500")
    ap.add_argument("--h", type=int, default=5)
    a = ap.parse_args()

    df0 = load("moex", a.target)
    lead0 = load("moex", a.leader)["Close"].reindex(df0.index).ffill()
    beta0 = float(np.sign((df0["Close"].pct_change().rolling(DAY_BARS, min_periods=DAY_BARS // 4)
                           .cov(lead0.pct_change())
                           / lead0.pct_change().rolling(DAY_BARS, min_periods=DAY_BARS // 4).var())
                          .median()))
    print(f"направление сделки: beta({a.target}~{a.leader}) знак {beta0:+.0f} -> "
          f"{'лонг' if beta0 > 0 else 'шорт'} {a.target} на ходе {a.leader} вверх")

    for k in (2, 3):
        rows, cost, d0, d1, gated_bars, cov = run_one(a.target, a.leader, k, a.h)
        if k == 2:
            print(f"=== {a.target} <- {a.leader}: {d0.date()}..{d1.date()}, "
                  f"издержки {cost:.2f} bp/круг ===")
            print("покрытие фильтра (событий всего / из них с известным порогом "
                  "-- где фильтра ещё физически не могло быть, warmup):")
            for name, (n_ev, n_elig) in cov.items():
                print(f"  {name:<18} {n_ev:>4} событий, {n_elig:>4} с известным порогом "
                      f"({0 if n_ev==0 else 100*n_elig/n_ev:.0f}%)")
        print(f"\n--- k={k}, h={a.h} ---")
        print(f"{'период':<18}{'режим':<12}{'n':>5}{'gross':>8}{'net':>7}"
              f"{'t(net)':>8}{'hit%':>7}{'sum_net':>10}{'maxDD':>8}")
        for name, label, n, mean, net_mean, net_t, hit, total, dd in rows:
            def f(v):
                return f"{v:.2f}" if v == v else "  --"
            print(f"{name:<18}{label:<12}{n:>5}{f(mean):>8}{f(net_mean):>7}"
                  f"{f(net_t):>8}{f(hit):>7}{f(total):>10}{f(dd):>8}")


if __name__ == "__main__":
    main()
