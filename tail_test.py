"""Гипотеза хвостов: живёт ли край в дни крупных движений лидера.

Логика: результат 2020 года был не «проводник работает», а «в панике марта
событие k>=3sigma давало в 30 раз больше средней минуты». Если множитель хвостов
жив, то и в 2026 дни с крупным ходом лидера должны давать кратно больше
обычных. Если нет — класс гипотез о событиях закрывается целиком.

Считает одну и ту же структуру сделки (событие k*sigma, вход Open(t+1), выход
Close(t+1+h), издержки, фильтр стыка сессий) отдельно на днях топ-N% по
дневному |ходу| лидера и на остальных днях.
"""
import numpy as np
import pandas as pd
from event_study import thin, stat


def series(f, col="close"):
    d = pd.read_csv(f"moex_data/{f}.csv", parse_dates=["time"]).set_index("time").sort_index()
    return d[~d.index.duplicated()]


def tails(tgt_f, lead_f, k, h, cost, day_bars, label, quantiles=(0.90, 0.95)):
    d = series(tgt_f)
    l = series(lead_f)["close"].reindex(d.index).ffill()
    rl = l.pct_change(fill_method=None)
    rt = d["close"].pct_change(fill_method=None)
    sd = rl.where(rl != 0).rolling(day_bars, min_periods=day_bars // 8).std()
    beta = (rt.rolling(day_bars, min_periods=day_bars // 4).cov(rl)
            / rl.rolling(day_bars, min_periods=day_bars // 4).var())
    direction = float(np.sign(beta.median()))
    step = d.index.to_series().diff().dt.total_seconds() / 60
    ok = (step <= 5).fillna(False)
    hold = ok.rolling(h + 2).min().shift(-(h + 2)).fillna(0).astype(bool)
    ev = thin(((rl > k * sd).fillna(False) & ok & hold), h + 1)
    fw = (d["close"].shift(-1 - h) / d["open"].shift(-1) - 1) * 1e4 * direction

    # дневной |ход| лидера
    lday = l.resample("1D").last().dropna()
    dmove = lday.pct_change().abs() * 1e4
    day_of = pd.Series(d.index.normalize(), index=d.index)

    base = fw[ev].dropna()
    bm, bt, bn = stat(base)
    print(f"\n{label}   (k={k}, h={h}, издержки {cost} bp)")
    print(f"  ВСЕ дни:              n{bn:5d}  gross{bm:+6.2f} bp  net{bm-cost:+6.2f}  t{bt:+5.1f}")
    for q in quantiles:
        thr = dmove.quantile(q)
        hot_days = set(dmove[dmove >= thr].index.normalize())
        hot = ev & day_of.isin(hot_days)
        cold = ev & ~day_of.isin(hot_days)
        for mask, nm in ((hot, f"топ-{int((1-q)*100)}% дней"), (cold, "остальные дни")):
            x = fw[mask].dropna()
            m, t, n = stat(x)
            if n < 10:
                continue
            mult = m / bm if bm else float("nan")
            print(f"  {nm:<21} n{n:5d}  gross{m:+6.2f} bp  net{m-cost:+6.2f}  t{t:+5.1f}"
                  + (f"  множитель x{mult:.1f}" if nm.startswith('топ') else ""))
        print(f"    (порог: дневной ход лидера >= {thr:.0f} bp)")


if __name__ == "__main__":
    print("=" * 78)
    print("ЭТАЛОН: 2020, где край был (RI <- S&P). Проверяем, что множитель хвостов виден")
    print("=" * 78)
    tails("flow_RI", "inv_SP500", 2.0, 5, 1.5, 840, "RI <- S&P 500, 2020")
    tails("flow_RI", "inv_SP500", 3.0, 5, 1.5, 840, "RI <- S&P 500, 2020")
    print()
    print("=" * 78)
    print("ПРОВЕРКА: 2026")
    print("=" * 78)
    tails("iss_RI", "inv_BRENT_hist", 2.0, 5, 1.5, 900, "RI <- Brent, 2026")
    tails("iss_RI", "inv_BRENT_hist", 3.0, 5, 1.5, 900, "RI <- Brent, 2026")
    tails("iss_Si_2026", "inv_USDCNH_hist", 2.0, 3, 0.5, 900, "Si <- USD/CNH, 2026")
    tails("iss_Si_2026", "inv_USDCNH_hist", 3.0, 3, 0.5, 900, "Si <- USD/CNH, 2026")
