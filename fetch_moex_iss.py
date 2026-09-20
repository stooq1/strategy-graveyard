"""Минутные свечи FORTS с MOEX ISS (бесплатно, без ключа) -> moex_data/.

Зачем: вневыборочная проверка «проводник S&P -> Si/RI» на 2023-2026.

Два прохода, чтобы не качать лишнее:
  1. дневные свечи по всем контрактам семейства (дёшево) -> календарь фронта
     (контракт дня = максимум дневного объёма);
  2. минутки ТОЛЬКО за фронт-окно каждого контракта — ровно то, что попадёт в
     непрерывный ряд, без «хвостов» дальних серий.

Надёжность: 4 попытки на запрос с растущей паузой; контракт сохраняется только
если окно скачано целиком, частичный файл не пишется и не «залипает» —
перезапуск той же команды дозабирает недостающее.

ВАЖНО про время: ISS отдаёт МСК, файлы проекта — UTC, скрипт вычитает 3 часа.
Проверка: сессия FORTS должна лечь в 06:00-20:50 UTC (09:00-23:50 МСК).

Выход:
  moex_data/<КОД>.csv        — минутки контракта за его фронт-окно
  moex_data/iss_<СЕМЬЯ>.csv  — непрерывный ряд, это --target для conductor_check

Запуск:
  python3 fetch_moex_iss.py --probe
  python3 fetch_moex_iss.py --families Si,RI --from 2023-01-01
  python3 fetch_moex_iss.py --families Si --rebuild     # только пересобрать ряд
"""
import argparse
import glob
import json
import os
import time
import urllib.request

import pandas as pd

ISS = ("https://iss.moex.com/iss/engines/futures/markets/forts/securities/"
       "{sec}/candles.json?interval={iv}&from={frm}&till={till}&start={start}&iss.meta=off")


def codes(family, y0, y1):
    return [f"{family}{m}{y % 10}" for y in range(y0, y1 + 1) for m in "HMUZ"]


def get(url, tries=4):
    delay = 2.0
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if i == tries - 1:
                raise RuntimeError(f"{type(e).__name__}: {e}")
            time.sleep(delay)
            delay *= 2.5
    return None


def fetch(sec, frm, till, iv=1, pause=0.35, tag=""):
    """Все свечи за период. Бросает RuntimeError, если не удалось дочитать."""
    rows, cols, start = [], None, 0
    while True:
        js = get(ISS.format(sec=sec, iv=iv, frm=frm, till=till, start=start))
        block = js.get("candles", {})
        cols = block.get("columns", cols)
        data = block.get("data", [])
        if not data:
            break
        rows.extend(data)
        start += len(data)
        if tag and start % 20000 == 0:
            print(f"      {tag} {start}...", flush=True)
        time.sleep(pause)
    return pd.DataFrame(rows, columns=cols) if rows else None


def normalize(df, tz_shift):
    df = df.rename(columns={"begin": "time"})
    df["time"] = pd.to_datetime(df["time"]) - pd.Timedelta(hours=tz_shift)
    keep = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
    return (df[keep].dropna(subset=["time"]).drop_duplicates("time")
            .sort_values("time").set_index("time"))


def front_calendar(family, secs, frm, till):
    """{код: (первый_день_фронта, последний_день_фронта)} по дневному объёму."""
    daily = {}
    for sec in secs:
        try:
            d = fetch(sec, frm, till, iv=24)
        except RuntimeError as e:
            print(f"    {sec}: дневки не скачались ({e}) — пропуск")
            continue
        if d is None or d.empty:
            continue
        d = normalize(d, 0)
        daily[sec] = d["volume"]
        print(f"    {sec}: дневок {len(d)}, {d.index[0].date()} – {d.index[-1].date()}")
    if not daily:
        return {}
    chosen = pd.DataFrame(daily).idxmax(axis=1).dropna()
    win = {}
    for sec in daily:
        days = chosen[chosen == sec].index
        if len(days):
            win[sec] = (days.min().strftime("%Y-%m-%d"), days.max().strftime("%Y-%m-%d"))
    return win


def have_window(path, w_end):
    if not os.path.exists(path):
        return False
    try:
        d = pd.read_csv(path, usecols=["time"], parse_dates=["time"])
    except Exception:
        return False
    return not d.empty and d["time"].max() >= pd.Timestamp(w_end) - pd.Timedelta(days=3)


def rebuild(family, years=None):
    """years — множество последних цифр года ('3','4','5','6'); нужно, чтобы в
    ряд ISS не попали файлы архива 2020 (SiH0/SiM0/SiU0 подходят под маску)."""
    parts = []
    for f in sorted(glob.glob(f"moex_data/{family}[HMUZ][0-9].csv")):
        if years and os.path.basename(f)[-5] not in years:
            continue
        d = pd.read_csv(f, parse_dates=["time"]).set_index("time").sort_index()
        if d.empty:
            continue
        d["code"] = os.path.basename(f)[:-4]
        parts.append(d)
    if not parts:
        print(f"  {family}: нет файлов контрактов")
        return
    # На каждую минуту берём контракт, который в ЭТОТ день был фронтом по объёму.
    # Так лишние данные в файле контракта (например скачанные за всю его жизнь)
    # не могут подменить настоящий фронт.
    allp = pd.concat(parts)
    allp["day"] = allp.index.normalize()
    dayvol = allp.groupby(["day", "code"])["volume"].sum().unstack()
    chosen = dayvol.idxmax(axis=1)
    allp = (allp[allp["code"] == allp["day"].map(chosen)]
            .drop(columns=["day"]).sort_index())
    allp = allp[~allp.index.duplicated(keep="first")]
    out = f"moex_data/iss_{family}.csv"
    allp.to_csv(out)
    days = sorted(set(allp.index.normalize()))
    h = allp.index.hour.value_counts().sort_index()
    print(f"  {out}: {len(allp)} минут, {len(days)} дней, {allp.index[0]} – {allp.index[-1]}")
    print("    часы UTC: " + " ".join(f"{k}:{v}" for k, v in h.items()))
    gaps = pd.Series(days).diff().dt.days
    big = gaps[gaps > 5]
    if len(big):
        print(f"    !! пропусков больше 5 дней: {len(big)} — какие-то контракты недокачаны")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", default="Si,RI")
    ap.add_argument("--from", dest="frm", default="2023-01-01")
    ap.add_argument("--till", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--tz-shift", type=int, default=3)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    os.makedirs("moex_data", exist_ok=True)
    fams = [f.strip() for f in a.families.split(",") if f.strip()]

    yrs = {str(y % 10) for y in range(int(a.frm[:4]), int(a.till[:4]) + 1)}
    if a.rebuild:
        for fam in fams:
            rebuild(fam, yrs)
        return

    if a.probe:
        for sec, day in (("SiZ5", "2025-11-20"), ("RIZ5", "2025-11-20"), ("SiZ6", a.till)):
            try:
                df = fetch(sec, day, day)
            except RuntimeError as e:
                print(f"  {sec} {day}: ошибка {e}")
                continue
            if df is None or df.empty:
                print(f"  {sec} {day}: пусто")
                continue
            d = normalize(df, a.tz_shift)
            print(f"  {sec} {day}: {len(d)} свечей, {d.index[0]} – {d.index[-1]}, "
                  f"close {d['close'].min():.0f}-{d['close'].max():.0f}")
        return

    y0, y1 = int(a.frm[:4]), int(a.till[:4])
    for fam in fams:
        print(f"{fam}: 1/2 дневки и календарь фронта")
        win = front_calendar(fam, codes(fam, y0, y1), a.frm, a.till)
        if not win:
            print(f"  {fam}: календарь не построен")
            continue
        order = sorted(win.items(), key=lambda x: x[1])
        print("  фронт: " + ", ".join(f"{k} {v[0]}..{v[1]}" for k, v in order))
        print(f"{fam}: 2/2 минутки за фронт-окна")
        failed = []
        for sec, (w0, w1) in order:
            path = f"moex_data/{sec}.csv"
            if have_window(path, w1):
                print(f"    {sec}: уже есть за {w0}..{w1}")
                continue
            try:
                df = fetch(sec, w0, w1, tag=sec)
            except RuntimeError as e:
                print(f"    {sec}: НЕ СКАЧАН ({e})")
                failed.append(sec)
                continue
            if df is None or df.empty:
                print(f"    {sec}: пусто")
                failed.append(sec)
                continue
            d = normalize(df, a.tz_shift)
            d.to_csv(path)
            print(f"    {sec}: {len(d)} минут, {d.index[0]} – {d.index[-1]}")
        if failed:
            print(f"  !! не скачались: {', '.join(failed)} — перезапустите ту же команду")
        rebuild(fam, yrs)


if __name__ == "__main__":
    main()
