"""Непрерывные ряды из контрактов для event_study --leader.

1. investing.com: сшивка цепочки контрактов одного индекса (S&P 500: Mar_20 ->
   Jun_20 -> Sep_20 -> US_500_Sep_20 и т.д.) с ratio-корректировкой на стыке,
   чтобы ролл не выглядел как «большой ход лидера». -> moex_data/inv_<NAME>.csv
2. FORTS: непрерывный фронт по семейству (BR, SR, GZ, MX, Si, RI): на каждый день
   берётся контракт с наибольшим дневным объёмом. -> moex_data/flow_<FAMILY>.csv
   (все колонки flow_*.csv, цены сырые — стык между контрактами приходится на
   границу дня).

Запуск: python build_continuous.py
"""
import glob
import os

import pandas as pd

INV_CHAINS = {
    "SP500": ["S_P_500_Mar_20", "S_P_500_Jun_20", "S_P_500_Sep_20", "US_500_Sep_20"],
    "NASDAQ": ["Nasdaq_Mar_20", "Nasdaq_Jun_20", "Nasdaq_Sep_20", "US_Tech_100_Sep_20"],
    "DOW": ["Dow_30_Mar_20", "Dow_30_Jun_20", "Dow_Jones_Jun_20", "Dow_Jones_30_Sep_20", "US_30_Sep_20"],
    "DAX": ["DAX_Mar_20", "DAX_Jun_20", "DAX_Sep_20"],
    "VIX": ["S_P_500_VIX_Mar_20", "S_P_500_VIX_Apr_20", "S_P_500_VIX_May_20", "S_P_500_VIX_Jun_20",
            "S_P_500_VIX_Jul_20", "S_P_500_VIX_Aug_20", "S_P_500_VIX_Sep_20"],
    "RTSinv": ["RTS_Mar_20", "RTS_Jun_20", "RTS_Sep_20"],
}
FAMILIES = ["BR", "SR", "GZ", "MX", "Si", "RI"]


def stitch_inv(name, codes):
    segs = []
    for c in codes:
        p = f"moex_data/inv_{c}.csv"
        if not os.path.exists(p):
            print(f"  {name}: нет {p}, пропуск"); continue
        s = pd.read_csv(p, parse_dates=["time"]).set_index("time").sort_index()
        s = s[~s.index.duplicated()]
        segs.append(s)
    segs.sort(key=lambda s: s.index[0])
    out, factor = None, 1.0
    for s in segs:
        if out is None:
            out = s.copy(); continue
        s = s[s.index > out.index[-1]]          # без пересечений: новый контракт после конца старого
        if len(s) == 0:
            continue
        factor = out["close"].iloc[-1] / s["close"].iloc[0]   # ratio-стык
        s = s.copy(); s["close"] = s["close"] * factor
        out = pd.concat([out, s])
    out.to_csv(f"moex_data/inv_{name}.csv")
    print(f"  inv_{name}.csv: {len(out)} минут, {out.index[0]} – {out.index[-1]}, сегментов {len(segs)}")


def front_family(fam):
    files = sorted(glob.glob(f"moex_data/flow_{fam}[A-Z]0.csv"))
    if not files:
        print(f"  {fam}: нет файлов"); return
    parts = []
    for f in files:
        code = os.path.basename(f)[5:-4]
        df = pd.read_csv(f, parse_dates=["time"]).set_index("time").sort_index()
        df["code"] = code
        parts.append(df)
    allp = pd.concat(parts)
    allp["day"] = allp.index.normalize()
    dayvol = allp.groupby(["day", "code"])["volume"].sum().unstack()
    front = dayvol.idxmax(axis=1)                     # контракт дня = максимум объёма
    keep = allp[allp["code"] == allp["day"].map(front)].drop(columns=["day"]).sort_index()
    keep.to_csv(f"moex_data/flow_{fam}.csv")
    rolls = front[front != front.shift()].to_dict()
    print(f"  flow_{fam}.csv: {len(keep)} минут; фронт по дням: "
          + ", ".join(f"{d.date()}→{c}" for d, c in rolls.items()))


if __name__ == "__main__":
    print("investing:")
    for name, codes in INV_CHAINS.items():
        stitch_inv(name, codes)
    print("FORTS:")
    for fam in FAMILIES:
        front_family(fam)
