"""Триаж лидеров: кто опережает цель хотя бы на минуту.

Смысл: β и корреляция на лаге 0 не значат ничего для торговли — связь может
быть сильной и при этом целиком внутриминутной (BTC vs SPY: 0.40 на лаге 0,
0.00 на лаге 1 — торговать нечего). Торгуется только ЗАПАЗДЫВАНИЕ. Скрипт по
каждому кандидату печатает:

  покрытие — доля минут цели, где лидер реально обновлялся (застывшая лента
             даёт фальшивый ноль, это надо видеть);
  β        — медиана скользящей беты;
  lag 0    — одновременная корреляция (справочно);
  lag +1..+3 — корреляция цели[t] с лидером[t-lag]: ЭТО и есть предмет поиска;
  lag -1,-2  — обратная сторона: если она больше прямой, лидер на самом деле
             следует за целью, а не наоборот.

Плюс многофакторная модель: OLS цели на ВСЕ лидеры с лагом 1 — отвечает на
вопрос «а если взять их в связку». R² lag1 и есть доля движения цели,
предсказуемая по вчерашней минуте всех лидеров вместе.

Запуск:
  python3 lead_triage.py --target iss_Si --leaders inv_SP500_hist,inv_BRENT_hist,inv_USDCNH_hist
  python3 lead_triage.py --source crypto --target BTCUSDT --leaders idx_gate_SPY
"""
import argparse
import os

import numpy as np
import pandas as pd


def load(source, sym):
    folder = "crypto_data" if source == "crypto" else "moex_data"
    p = f"{folder}/{sym}.csv"
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    d = pd.read_csv(p, parse_dates=["time"]).set_index("time").sort_index()
    d = d[~d.index.duplicated(keep="first")]
    col = "close" if "close" in d.columns else "Close"
    return d[col]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="moex")
    ap.add_argument("--target", required=True)
    ap.add_argument("--leaders", required=True)
    ap.add_argument("--day-bars", type=int, default=None)
    ap.add_argument("--max-gap", type=int, default=5)
    ap.add_argument("--from", dest="frm", default=None, help="срез цели, YYYY-MM-DD")
    ap.add_argument("--till", default=None)
    a = ap.parse_args()
    if a.day_bars is None:
        a.day_bars = 1440 if a.source == "crypto" else 900

    tgt = load(a.source, a.target)
    if a.frm:
        tgt = tgt.loc[a.frm:]
    if a.till:
        tgt = tgt.loc[:a.till + " 23:59"]
    rt = tgt.pct_change(fill_method=None)
    step = tgt.index.to_series().diff().dt.total_seconds() / 60
    ok = (step <= a.max_gap).fillna(False) if a.max_gap else pd.Series(True, index=tgt.index)
    rt = rt.where(ok)
    print(f"цель {a.target}: {len(tgt)} минут, {tgt.index[0]} – {tgt.index[-1]}, "
          f"баров на стыке сессий отброшено {int((~ok).sum())}\n")

    names = [x.strip() for x in a.leaders.split(",") if x.strip()]
    print(f"{'лидер':<24} {'покрытие':>9} {'β':>7} {'lag0':>7} {'lag+1':>7} "
          f"{'lag+2':>7} {'lag+3':>7} {'lag-1':>7} {'lag-2':>7}")
    print("-" * 94)
    R = {}
    for nm in names:
        try:
            L = load(a.source, nm)
        except FileNotFoundError as e:
            print(f"{nm:<24} нет файла {e}")
            continue
        l = L.reindex(tgt.index).ffill()
        cover = float(L.reindex(tgt.index).notna().mean())
        rl = l.pct_change(fill_method=None).where(ok)
        if cover < 0.10:
            print(f"{nm:<24} {cover:>8.0%}   ПРОПУЩЕН: почти нет пересечения с целью "
                  f"({L.index[0].date()}..{L.index[-1].date()} против "
                  f"{tgt.index[0].date()}..{tgt.index[-1].date()})")
            continue
        R[nm] = rl
        beta = (rt.rolling(a.day_bars, min_periods=a.day_bars // 4).cov(rl)
                / rl.rolling(a.day_bars, min_periods=a.day_bars // 4).var()).median()
        cc = {lag: float(rt.corr(rl.shift(lag))) for lag in (0, 1, 2, 3, -1, -2)}
        print(f"{nm:<24} {cover:>8.0%} {beta:>+7.3f} " +
              " ".join(f"{cc[lag]:>+7.3f}" for lag in (0, 1, 2, 3, -1, -2)))

    if len(R) >= 2:
        X = pd.DataFrame(R)
        for lag, label in ((0, "lag 0 (справочно)"), (1, "lag +1 (торгуемое)")):
            M = pd.concat([rt.rename("y"), X.shift(lag)], axis=1).dropna()
            if len(M) < 1000:
                continue
            y = M["y"].values
            A = np.column_stack([np.ones(len(M)), M[list(X.columns)].values])
            coef, *_ = np.linalg.lstsq(A, y, rcond=None)
            pred = A @ coef
            r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()
            print(f"\nсвязка всех лидеров, {label}: R² = {r2:.4f}, n = {len(M)}")
            if lag == 1:
                sd = float(np.std(pred)) * 1e4
                print(f"  σ предсказанного хода = {sd:.2f} bp/мин — сравнивать с издержками")
                for nm, c in zip(X.columns, coef[1:]):
                    print(f"    {nm:<24} коэффициент {c:+.3f}")


if __name__ == "__main__":
    main()
