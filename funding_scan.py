"""Скан экстремального фандинга по ВСЕМ USDT-перпам Binance: сколько на $10k дал бы
захеджированный керри (шорт перпа + лонг спота) на эпизодах, когда ставка улетает.

Зачем: единственный класс края, где $10k — преимущество, а не недостаток
(ёмкость: фонды в мелкие перпы не заходят). Проверяем ДО того, как верить.

Правило эпизода объявлено заранее и не подбирается:
  вход  — после первой выплаты со ставкой ≥ --enter (% годовых; по умолчанию 100),
          только если у монеты есть спотовая пара к USDT на Binance (иначе не захеджировать);
  выход — после первой выплаты со ставкой < --exit (по умолчанию 30% годовых);
  доход — сумма полученных выплат × нотионал (шорт перпа получает положительный фандинг);
  издержки — --cost bp нотионала за эпизод (по умолчанию 50: два круга тейкером
          спот+перп ≈ 30 bp плюс проскальзывание на мелочи);
  базис игнорируется — консервативно: при высоком фандинге перп торгуется с премией,
          шорт входит дорого и закрывается после схлопывания премии, это плюс, не минус.
Капитальная симуляция: $10k, не больше --slots одновременных позиций равного размера,
при нехватке слотов эпизод пропускается. Рядом — верхняя граница «взяли бы всё по $10k».

Ограничения (честно): exchangeInfo отдаёт только ТЕКУЩИЕ символы — делистнутые перпы
в скан не попадают (survivorship); ставка следующей выплаты на Binance видна заранее,
так что вход «после первой выплаты» — консервативная аппроксимация; риски делистинга,
невозможности закрыть спот и ухода базиса в цифрах не учтены.

Данные кэшируются в crypto_data/daily_binance/funding_<SYM>.csv (тот же формат, что у
fetch_daily.py), повторный запуск докачивает хвост; список символов — crypto_data/perp_symbols.csv.

Запуск (первый раз ~10–20 минут, лучше фоном; --scan без сети на том, что скачано):
  .venv/bin/python funding_scan.py --fetch --scan
  .venv/bin/python funding_scan.py --scan --enter 50
Результат: funding_scan_episodes.csv (все эпизоды), funding_scan_by_year.csv, печать сводки.
"""
import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

import fetch_daily as fd

D_OUT = "crypto_data/daily_binance"
SYMS = "crypto_data/perp_symbols.csv"
HOURS_PER_YEAR = 8760.0


# ---------------------------------------------------------------- загрузка

def list_symbols():
    """USDT-перпы Binance (текущие) + дата листинга + есть ли спот к USDT."""
    info = fd.get_json(f"{fd.FAPI}/fapi/v1/exchangeInfo")
    perps = [(s["symbol"], s.get("baseAsset", ""), int(s.get("onboardDate", 0) or 0), s.get("status", ""))
             for s in info["symbols"]
             if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"]
    spot = fd.get_json("https://api.binance.com/api/v3/exchangeInfo")
    spot_bases = {s["baseAsset"] for s in spot["symbols"]
                  if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"}
    rows = [dict(symbol=sym, base=base, onboard=pd.to_datetime(ob, unit="ms") if ob else pd.NaT,
                 status=st, spot=base in spot_bases) for sym, base, ob, st in perps]
    d = pd.DataFrame(rows).sort_values("symbol").set_index("symbol")
    fd.save_atomic(d, SYMS)
    return d


def fetch_one(sym, start_ms):
    path = f"{D_OUT}/funding_{sym}.csv"
    old = fd.read_cached(path)
    s = start_ms if old is None else int(old.index[-1].timestamp() * 1000) + 1
    try:
        new = fd.binance_funding(sym, s, pause=0.1)
    except RuntimeError as e:
        return sym, None, str(e)
    if new is None and old is None:
        return sym, 0, None
    f = fd.merge_save(old, new, path) if new is not None else old
    return sym, len(f), None


def fetch_all(frm, workers=4):
    syms = list_symbols()
    print(f"перпов USDT на Binance: {len(syms)}, из них со спотом к USDT: {int(syms['spot'].sum())}", flush=True)
    start_ms = int(pd.Timestamp(frm).timestamp() * 1000)
    failed, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for sym, n, err in ex.map(lambda s: fetch_one(s, start_ms), list(syms.index)):
            done += 1
            if err:
                failed.append(sym)
            if done % 25 == 0 or done == len(syms):
                print(f"  {done}/{len(syms)} символов, {time.time() - t0:.0f} с"
                      + (f", ошибок {len(failed)}" if failed else ""), flush=True)
    if failed:
        print(f"  !! не скачались: {', '.join(failed[:20])}{'…' if len(failed) > 20 else ''} — "
              f"перезапустите ту же команду", flush=True)


# ------------------------------------------------------------------- скан

def load_payments(syms):
    """{символ: DataFrame[rate, hours, ann]} — ставка выплаты, интервал до неё, % годовых."""
    out = {}
    for sym in syms.index:
        f = fd.read_cached(f"{D_OUT}/funding_{sym}.csv")
        if f is None or len(f) < 3:
            continue
        r = f["funding_rate"].astype(float)
        hours = r.index.to_series().diff().dt.total_seconds().div(3600).clip(1, 8).fillna(8.0)
        out[sym] = pd.DataFrame({"rate": r, "hours": hours, "ann": r * HOURS_PER_YEAR / hours * 100})
    return out


def episodes(sym, p, enter, exit_, cost_bp, confirm=1):
    """Эпизоды по правилу из докстринга. P&L в долях нотионала.
    confirm — сколько выплат подряд должны быть ≥ enter перед входом (1 = сразу)."""
    eps, i, n = [], 0, len(p)
    ann, rate, ts = p["ann"].values, p["rate"].values, p.index
    while i < n:
        if i < confirm - 1 or (ann[i - confirm + 1:i + 1] < enter).any():
            i += 1
            continue
        j, got, n_pay = i + 1, 0.0, 0
        while j < n:
            got += rate[j]
            n_pay += 1
            if ann[j] < exit_:
                break
            j += 1
        if n_pay == 0:          # сигнал на самой последней выплате — позиция ещё ничего не получила
            break
        j = min(j, n - 1)
        eps.append(dict(symbol=sym, start=ts[i], end=ts[j], payments=n_pay,
                        hours=(ts[j] - ts[i]).total_seconds() / 3600, ann_entry=ann[i],
                        gross=got, net=got - cost_bp / 1e4, year=ts[i].year))
        i = j + 1
    return eps


def simulate(eps, capital, slots, cost_bp):
    """$capital, не больше slots одновременных позиций по capital/slots; эпизоды по времени."""
    eps = sorted(eps, key=lambda e: (e["start"], -e["ann_entry"]))
    size, open_until, taken = capital / slots, [], []
    for e in eps:
        open_until = [t for t in open_until if t > e["start"]]
        if len(open_until) >= slots:
            continue
        open_until.append(e["end"])
        taken.append(dict(e, usd=e["net"] * size))
    return taken


def scan(enter, exit_, cost_bp, capital, slots, only_spot=True, confirm=1):
    syms = pd.read_csv(SYMS, parse_dates=["onboard"]).set_index("symbol")
    if only_spot:
        syms = syms[syms["spot"]]
    pays = load_payments(syms)
    if not pays:
        print("нет данных — сначала --fetch")
        return
    eps = []
    for sym, p in pays.items():
        eps.extend(episodes(sym, p, enter, exit_, cost_bp, confirm))
    first = min(p.index[0] for p in pays.values())
    last = max(p.index[-1] for p in pays.values())
    years = (last - first).days / 365.25
    print(f"Символов с данными: {len(pays)} ({'со спотом' if only_spot else 'все'}), период {first.date()} – {last.date()}, "
          f"вход ≥ {enter:.0f}% годовых {confirm} выплат{'а' if confirm == 1 else ''} подряд, выход < {exit_:.0f}%, "
          f"издержки {cost_bp:.0f} bp/эпизод")
    if not eps:
        print("эпизодов нет")
        return
    E = pd.DataFrame(eps)
    E["days"] = E["hours"] / 24
    taken = pd.DataFrame(simulate(eps, capital, slots, cost_bp))
    print(f"\nЭпизодов всего {len(E)} ({len(E) / years:.0f} в год), медиана длительности {E['days'].median():.1f} дн, "
          f"в плюсе после издержек {(E['net'] > 0).mean() * 100:.0f}%, медианный net {E['net'].median() * 100:+.2f}% нотионала, "
          f"средний {E['net'].mean() * 100:+.2f}%")
    print(f"Верхняя граница «взяли всё по ${capital:,.0f}»: {E['net'].sum() * capital / years:+,.0f} $/год")
    if len(taken):
        print(f"Симуляция ${capital:,.0f}, {slots} слота по ${capital / slots:,.0f}: взято {len(taken)} эпизодов, "
              f"{taken['usd'].sum() / years:+,.0f} $/год, худший эпизод {taken['usd'].min():+,.0f} $, "
              f"лучший {taken['usd'].max():+,.0f} $")
    else:
        taken = pd.DataFrame(columns=["year", "usd", "start"])
        print("Симуляция: ни один эпизод не взят")

    by = E.groupby("year").agg(эпизодов=("net", "size"), в_плюсе=("net", lambda x: (x > 0).mean() * 100),
                               медиана_net_pct=("net", lambda x: x.median() * 100),
                               сумма_net_pct=("net", lambda x: x.sum() * 100))
    by["взято"] = taken.groupby("year")["usd"].size() if len(taken) else 0
    by["сим_usd"] = taken.groupby("year")["usd"].sum() if len(taken) else 0.0
    by["верх_usd"] = by["сумма_net_pct"] / 100 * capital
    by = by.fillna(0)
    print("\nПо годам (сим_usd — симуляция $10k/слоты; верх_usd — если брать всё по $10k):")
    print(by.round(1).to_string())
    by.to_csv("funding_scan_by_year.csv")

    top = E.sort_values("net", ascending=False).head(15)
    print("\nТоп-15 эпизодов (net % нотионала):")
    for _, e in top.iterrows():
        print(f"  {e['symbol']:<14} {e['start'].date()} → {e['end'].date()}  {e['days']:5.1f} дн  "
              f"вход {e['ann_entry']:6.0f}%/год  выплат {e['payments']:3d}  net {e['net'] * 100:+.2f}%")
    worst = E.sort_values("net").head(5)
    print("Худшие 5:")
    for _, e in worst.iterrows():
        print(f"  {e['symbol']:<14} {e['start'].date()} → {e['end'].date()}  {e['days']:5.1f} дн  "
              f"вход {e['ann_entry']:6.0f}%/год  net {e['net'] * 100:+.2f}%")
    conc = E.nlargest(5, "net")["net"].sum() / E["net"].sum() * 100 if E["net"].sum() > 0 else float("nan")
    print(f"\nКонцентрация: топ-5 эпизодов = {conc:.0f}% суммарного net; "
          f"последние 12 мес: {E[E['start'] >= last - pd.Timedelta(days=365)]['net'].sum() * capital:+,.0f} $ по верхней границе, "
          f"{taken[taken['start'] >= last - pd.Timedelta(days=365)]['usd'].sum():+,.0f} $ по симуляции")
    # свежие листинги: фандинг в первые 14 дней
    ob = syms["onboard"].dropna()
    rows = []
    for sym, t0 in ob.items():
        if sym in pays:
            p = pays[sym].loc[t0:t0 + pd.Timedelta(days=14)]
            if len(p) >= 6:
                rows.append(dict(symbol=sym, listed=t0.date(), ann14=p["rate"].sum() / max(1e-9, p["hours"].sum()) * HOURS_PER_YEAR * 100))
    if rows:
        L = pd.DataFrame(rows)
        print(f"\nСвежие листинги ({len(L)} шт. с данными за первые 14 дней): средний фандинг "
              f"{L['ann14'].mean():+.0f}% годовых, медиана {L['ann14'].median():+.0f}%, "
              f"доля выше 100%: {(L['ann14'] > 100).mean() * 100:.0f}%")
    E.to_csv("funding_scan_episodes.csv", index=False)
    print("\nЗаписано: funding_scan_episodes.csv, funding_scan_by_year.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="скачать/докачать фандинг по всем перпам")
    ap.add_argument("--scan", action="store_true", help="посчитать эпизоды на скачанном")
    ap.add_argument("--from", dest="frm", default="2020-01-01")
    ap.add_argument("--enter", type=float, default=100.0, help="порог входа, % годовых")
    ap.add_argument("--exit", dest="exit_", type=float, default=30.0, help="порог выхода, % годовых")
    ap.add_argument("--cost", type=float, default=50.0, help="издержки за эпизод, bp нотионала")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--slots", type=int, default=3)
    ap.add_argument("--all-symbols", action="store_true", help="считать и те, у кого нет спота (незахеджируемые)")
    ap.add_argument("--confirm", type=int, default=1, help="выплат подряд ≥ порога перед входом (фильтр ложных стартов)")
    a = ap.parse_args()
    os.makedirs(D_OUT, exist_ok=True)
    if a.fetch:
        fetch_all(a.frm)
    if a.scan or not a.fetch:
        if not os.path.exists(SYMS):
            print("нет crypto_data/perp_symbols.csv — сначала --fetch")
            return
        scan(a.enter, a.exit_, a.cost, a.capital, a.slots, only_spot=not a.all_symbols, confirm=a.confirm)


if __name__ == "__main__":
    main()
