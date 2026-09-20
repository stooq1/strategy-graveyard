"""Разбор эффекта «проводник → инструмент» (lead-lag после большого хода лидера).

Для событий «лидер сделал ход > k·σ за минуту t» считает доходность цели
от Open(t+1) до Close(t+1+h) в сторону хода лидера (bp), с прореживанием
событий, и раскладывает:
  1. по периодам (контракты RI: H0 / M0 / U0) — держится ли знак;
  2. по порогу k (1.5 / 2 / 3σ) — растёт ли эффект с размером хода;
  3. по часам UTC с n — где живёт (16:30 МСК = 13:30 UTC);
  4. по тому, успела ли цель отреагировать в минуту t
     («отстала» = ход цели < 0.5·β·ход лидера) — пружина на импульсе;
  5. концентрация: доля результата в топ-5 днях.

Запуск: python conductor_check.py --target flow_RI --leader inv_SP500 [--h 5]
"""
import argparse
import warnings

import numpy as np
import pandas as pd

from event_study import load, fwd_bp, thin, stat

warnings.filterwarnings("ignore")
PERIODS = {"H0 (25.02–19.03)": ("2020-02-25", "2020-03-19"),
           "M0 (20.03–18.06)": ("2020-03-20", "2020-06-18"),
           "U0 (19.06–03.09)": ("2020-06-19", "2020-09-03")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="flow_RI")
    ap.add_argument("--leader", default="inv_SP500")
    ap.add_argument("--source", default="moex")
    ap.add_argument("--h", type=int, default=5)
    ap.add_argument("--day-bars", type=int, default=None,
                    help="баров в сутках (moex 840, crypto 1440)")
    ap.add_argument("--max-gap", type=int, default=5,
                    help="максимальный разрыв между барами, мин: события на стыке "
                         "сессий отбрасываются (0 = не фильтровать)")
    a = ap.parse_args()
    if a.day_bars is None:
        a.day_bars = 1440 if a.source == "crypto" else 840

    df = load(a.source, a.target)
    lead = load(a.source, a.leader)["Close"].reindex(df.index).ffill()
    r_l, r_t = lead.pct_change(), df["Close"].pct_change()
    sd = r_l.where(r_l != 0).rolling(a.day_bars, min_periods=a.day_bars // 8).std()
    beta = (r_t.rolling(a.day_bars, min_periods=a.day_bars // 4).cov(r_l)
            / r_l.rolling(a.day_bars, min_periods=a.day_bars // 4).var())
    sign = float(np.sign(beta.median()))
    H = [1, 2, 3, 5, 10, 15, 30]
    fw = {h: fwd_bp(df, h) for h in H}
    # Стык сессий: у инструмента с перерывом (FORTS 20:50->06:00) «ход лидера за
    # минуту» на первом баре — это движение за всю ночь, а h-минутный отклик
    # может перепрыгнуть через ночь. И то и другое — не событие гипотезы.
    step = df.index.to_series().diff().dt.total_seconds() / 60
    ok_in = (step <= a.max_gap) if a.max_gap else pd.Series(True, index=df.index)
    ok_in = ok_in.fillna(False)
    ok_hold = {h: ok_in.rolling(h + 2).min().shift(-(h + 2)).fillna(0).astype(bool)
               for h in H}

    if a.max_gap:
        drop = int((~ok_in).sum())
        print(f"    фильтр стыка сессий: баров с разрывом > {a.max_gap} мин — {drop} "
              f"({drop / len(df):.1%}), события на них и сделки через разрыв исключены")
    print(f"=== {a.target} <- {a.leader}: {len(df)} мин, β={beta.median():+.2f}, "
          f"1 тик = {(df['Close'].diff().abs().replace(0, np.nan).min() / df['Close'].median() * 1e4):.2f} bp")

    def ev(k, side):
        m = (r_l > k * sd) if side > 0 else (r_l < -k * sd)
        m = m.fillna(False)
        if a.max_gap:
            m = m & ok_in
        return m, side * sign

    def line(label, mask, direction, h=a.h, hs=H):
        out = []
        for hh in hs:
            m = thin(mask & ok_hold[hh] if a.max_gap else mask, hh + 1)
            mean, t, n = stat(fw[hh][m] * direction)
            out.append(f"h{hh}:{mean:+5.1f}bp t{t:+4.1f} n{n}")
        print(f"  {label:<34} " + " | ".join(out))

    print("\n1. По порогу k и стороне (все периоды):")
    for k in (1.5, 2.0, 3.0):
        for side, nm in ((+1, "лидер ВВЕРХ"), (-1, "лидер ВНИЗ")):
            mask, d = ev(k, side)
            line(f"k={k} {nm}", mask, d, hs=[1, 2, 5, 15])

    periods = PERIODS
    if a.source == "crypto":   # календарные месяцы вместо контрактов
        months = sorted(set(df.index.strftime("%Y-%m")))
        periods = {m: (m + "-01", (pd.Timestamp(m + "-01") + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d"))
                   for m in months}
    elif not (df.index.min() <= pd.Timestamp("2020-09-03")
              and df.index.max() >= pd.Timestamp("2020-02-25")):
        # данные вне архива 2020 (например ISS 2023-2026) — режем по кварталам
        q = sorted(set(df.index.to_period("Q").astype(str)))
        periods = {x: (pd.Period(x).start_time.strftime("%Y-%m-%d"),
                       pd.Period(x).end_time.strftime("%Y-%m-%d")) for x in q}
    print(f"\n2. По периодам (k=2, h={a.h}):")
    for side, nm in ((+1, "ВВЕРХ"), (-1, "ВНИЗ")):
        mask, d = ev(2.0, side)
        for per, (s, e) in periods.items():
            pm = mask & (mask.index >= s) & (mask.index <= e + " 23:59")
            m = thin(pm & ok_hold[a.h] if a.max_gap else pm, a.h + 1)
            mean, t, n = stat(fw[a.h][m] * d)
            print(f"  {nm:<6} {per:<20} {mean:+6.1f}bp t{t:+5.1f} n{n}")

    print(f"\n3. По часам UTC (k=2, h={a.h}), mean bp / t / n:")
    for side, nm in ((+1, "ВВЕРХ"), (-1, "ВНИЗ")):
        mask, d = ev(2.0, side)
        m = thin(mask & ok_hold[a.h] if a.max_gap else mask, a.h + 1)
        x = fw[a.h][m] * d
        g = x.groupby(x.index.hour)
        print(f"  {nm}: " + "  ".join(f"{int(h)}:{v.mean():+.1f}/{v.mean()/(v.std()/np.sqrt(len(v))) if len(v)>2 and v.std()>0 else 0:+.1f}/{len(v)}"
                              for h, v in g if len(v) >= 10))

    print(f"\n4. Успела ли цель отреагировать в минуту t (k=2, h={a.h}):")
    for side, nm in ((+1, "ВВЕРХ"), (-1, "ВНИЗ")):
        mask, d = ev(2.0, side)
        implied = beta * r_l                          # сколько «должна» была пройти цель
        ratio = (r_t / implied).where(implied != 0)
        lagged = mask & (ratio < 0.5)
        reacted = mask & (ratio >= 0.5)
        for lab, mm in (("цель ОТСТАЛА (<50% от β·ход)", lagged), ("цель уже прошла ≥50%", reacted)):
            mm = mm.fillna(False)
            m = thin(mm & ok_hold[a.h] if a.max_gap else mm, a.h + 1)
            mean, t, n = stat(fw[a.h][m] * d)
            print(f"  {nm:<6} {lab:<30} {mean:+6.1f}bp t{t:+5.1f} n{n}")

    print(f"\n5. Концентрация по дням (k=2, h={a.h}):")
    for side, nm in ((+1, "ВВЕРХ"), (-1, "ВНИЗ")):
        mask, d = ev(2.0, side)
        m = thin(mask & ok_hold[a.h] if a.max_gap else mask, a.h + 1)
        x = (fw[a.h][m] * d).dropna()
        byday = x.groupby(x.index.date).sum().sort_values(ascending=False)
        tot = x.sum()
        top5 = byday.head(5).sum()
        pos_days = (byday > 0).mean()
        print(f"  {nm}: сумма {tot:+.0f}bp за {len(byday)} дней; топ-5 дней = {top5:+.0f}bp "
              f"({top5 / tot * 100 if tot else 0:.0f}%); дней с плюсом {pos_days:.0%}; "
              f"без топ-5: {(tot - top5) / max(len(x) - byday.head(5).index.size, 1):+.2f}bp/событие")


if __name__ == "__main__":
    main()
