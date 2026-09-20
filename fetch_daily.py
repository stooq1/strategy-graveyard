"""Дневные бары для трендовой проверки (п.1 плана от 18.09): USDT-перпы Bybit
(торгуемая площадка) и Binance (для сверки) + история фандинга, и FORTS с MOEX
ISS (дневная история по контрактам -> непрерывный фронт по объёму).

Зачем: тренд на горизонте дни-недели, где издержки 0.5-8 bp тонут в движении.
Крипта — торгуемая площадка; FORTS — вневыборочная проверка того же правила на
другом рынке (в дневках уже лежат март 2020 и февраль 2022, ждать обвал не надо).

Надёжность: 4 попытки на запрос с растущей паузой; файл пишется целиком через
временный + os.replace, частичных файлов нет; повторный запуск той же команды
докачивает только хвост (крипта) или недостающие контракты (FORTS).

Глубина ISS (проверено 18.09.2026): history по SECID отдаёт данные только для
контрактов, чей код не занят сейчас торгуемой серией. Коды *6/*7/*8 заняты
контрактами 2026-2028, поэтому 2016-2018 недоступны (пусто или обрывок 2017 без
2018). Надёжный ряд — с SiH9 (фронт с 12.2018); в нём есть март 2020 и февраль
2022. Дни с фиктивным фронтом (дальняя серия с копеечным объёмом) выбрасываются
при сборке.

Выход:
  crypto_data/daily_<биржа>/<SYM>.csv          time,open,high,low,close,volume,quote_volume  (UTC-день)
  crypto_data/daily_<биржа>/funding_<SYM>.csv  time,funding_rate  (каждая выплата, ставка за период)
  moex_data/daily/<КОД>_<ГГГГ>.csv     дневки контракта из ISS history (год в имени — коды повторяются раз в 10 лет)
  moex_data/daily/cont_<СЕМЬЯ>.csv     непрерывный фронт: time,code,close,volume,oi,ret
                                       (ret — доходность контракта, который был фронтом по объёму НАКАНУНЕ;
                                        стык контрактов в ret не попадает)

Запуск — сначала probe (один символ / один контракт, 20 секунд), потом целиком:
  .venv/bin/python fetch_daily.py --probe
  .venv/bin/python fetch_daily.py --bybit
  .venv/bin/python fetch_daily.py --binance
  .venv/bin/python fetch_daily.py --moex
  .venv/bin/python fetch_daily.py --moex --families Si,RI --from 2019   # выборочно
  .venv/bin/python fetch_daily.py --moex --rebuild                      # только пересобрать cont_*
"""
import argparse
import calendar
import glob
import json
import os
import time
import urllib.error
import urllib.request

import pandas as pd

FAPI = "https://fapi.binance.com"
BYBIT = "https://api.bybit.com"
ISS_HIST = ("https://iss.moex.com/iss/history/engines/futures/markets/forts/securities/"
            "{sec}.json?from={frm}&till={till}&start={start}&iss.meta=off"
            "&iss.only=history,history.cursor"
            "&history.columns=TRADEDATE,SECID,OPEN,LOW,HIGH,CLOSE,SETTLEPRICE,VOLUME,OPENPOSITION,NUMTRADES")

# Ликвидные USDT-перпы с историей с 2019-2021 (одни и те же тикеры на Bybit и Binance).
# Нет символа на бирже -> пропуск с сообщением.
CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "SOLUSDT",
                  "DOGEUSDT", "LTCUSDT", "LINKUSDT", "DOTUSDT", "AVAXUSDT", "ATOMUSDT",
                  "BCHUSDT", "ETCUSDT", "TRXUSDT", "XLMUSDT"]
# Семейство -> буквы месяцев экспирации (квартальные HMUZ, Brent — каждый месяц).
MOEX_FAMILIES = {"Si": "HMUZ", "RI": "HMUZ", "Eu": "HMUZ", "CR": "HMUZ", "GD": "HMUZ",
                 "GZ": "HMUZ", "SR": "HMUZ", "MX": "HMUZ", "BR": "FGHJKMNQUVXZ"}
MONTH_OF = {m: i + 1 for i, m in enumerate("FGHJKMNQUVXZ")}
DAY_MS = 86_400_000


# ----------------------------------------------------------------- общее

def get_json(url, tries=4):
    """JSON по URL с ретраями. 400/404 — «нет такого символа/контракта», без ретраев."""
    delay = 2.0
    for i in range(tries):
        try:
            # Браузерный UA: WAF Bybit отвечает 404 на «неизвестные» клиенты (index_collector — так же)
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
                "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                raise RuntimeError(f"HTTP {e.code}")
            if e.code in (418, 429):          # лимит запросов — ждём дольше
                delay = max(delay, 30.0)
            if i == tries - 1:
                raise RuntimeError(f"HTTP {e.code}")
        except Exception as e:
            if i == tries - 1:
                raise RuntimeError(f"{type(e).__name__}: {e}")
        time.sleep(delay)
        delay *= 2.5
    return None


def save_atomic(df, path):
    """Пишем во временный файл и подменяем — либо файл целиком, либо старый."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    df.to_csv(tmp)
    os.replace(tmp, path)


def read_cached(path, index_col="time"):
    if not os.path.exists(path):
        return None
    try:
        d = pd.read_csv(path, parse_dates=[index_col]).set_index(index_col).sort_index()
        return d if not d.empty else None
    except Exception:
        return None


# ---------------------------------------------------------------- Binance

def binance_klines(sym, start_ms, pause=0.25):
    """Дневные бары с start_ms до сейчас (незакрытый текущий день отбрасывается)."""
    rows, now_ms = [], int(time.time() * 1000)
    while True:
        js = get_json(f"{FAPI}/fapi/v1/klines?symbol={sym}&interval=1d&limit=1500&startTime={start_ms}")
        if not js:
            break
        rows.extend(js)
        start_ms = js[-1][0] + DAY_MS
        if len(js) < 1500:
            break
        time.sleep(pause)
    rows = [r for r in rows if r[6] < now_ms]         # closeTime в прошлом = день закрыт
    if not rows:
        return None
    d = pd.DataFrame([[r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                       float(r[5]), float(r[7]), int(r[8])] for r in rows],
                     columns=["time", "open", "high", "low", "close", "volume",
                              "quote_volume", "n_trades"])
    d["time"] = pd.to_datetime(d["time"], unit="ms")
    return d.drop_duplicates("time").set_index("time").sort_index()


def binance_funding(sym, start_ms, pause=0.25):
    rows = []
    while True:
        js = get_json(f"{FAPI}/fapi/v1/fundingRate?symbol={sym}&limit=1000&startTime={start_ms}")
        if not js:
            break
        rows.extend(js)
        start_ms = int(js[-1]["fundingTime"]) + 1
        if len(js) < 1000:
            break
        time.sleep(pause)
    if not rows:
        return None
    d = pd.DataFrame({"time": pd.to_datetime([int(r["fundingTime"]) for r in rows], unit="ms"),
                      "funding_rate": [float(r["fundingRate"]) for r in rows]})
    return d.drop_duplicates("time").set_index("time").sort_index()


def merge_save(old, new, path):
    allp = new if old is None else pd.concat([old, new])
    allp = allp[~allp.index.duplicated(keep="last")].sort_index()
    save_atomic(allp, path)
    return allp


# ------------------------------------------------------------------ Bybit

def bybit_result(url):
    js = get_json(url)
    if js.get("retCode") != 0:
        raise RuntimeError(f"bybit {js.get('retCode')}: {js.get('retMsg')}")
    return js["result"].get("list") or []


def bybit_klines(sym, start_ms, pause=0.2):
    """Дневные бары v5 (category=linear) с start_ms до сейчас; листаем назад по end —
    так работает и первая загрузка, и докачка хвоста. Незакрытый день отбрасывается."""
    rows, now_ms, end = [], int(time.time() * 1000), None
    while True:
        url = f"{BYBIT}/v5/market/kline?category=linear&symbol={sym}&interval=D&limit=1000"
        lst = bybit_result(url + (f"&end={end}" if end else ""))
        if not lst:
            break
        rows.extend(lst)
        oldest = min(int(r[0]) for r in lst)
        if oldest <= start_ms or len(lst) < 1000:
            break
        end = oldest - 1
        time.sleep(pause)
    rows = [r for r in rows if start_ms <= int(r[0]) and int(r[0]) + DAY_MS <= now_ms]
    if not rows:
        return None
    d = pd.DataFrame([[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                       float(r[5]), float(r[6])] for r in rows],
                     columns=["time", "open", "high", "low", "close", "volume", "quote_volume"])
    d["time"] = pd.to_datetime(d["time"], unit="ms")
    return d.drop_duplicates("time").set_index("time").sort_index()


def bybit_funding(sym, start_ms, pause=0.2):
    rows, end = [], None
    while True:
        url = f"{BYBIT}/v5/market/funding-history?category=linear&symbol={sym}&limit=200"
        lst = bybit_result(url + (f"&endTime={end}" if end else ""))
        if not lst:
            break
        rows.extend(lst)
        oldest = min(int(r["fundingRateTimestamp"]) for r in lst)
        if oldest <= start_ms or len(lst) < 200:
            break
        end = oldest - 1
        time.sleep(pause)
    rows = [r for r in rows if int(r["fundingRateTimestamp"]) >= start_ms]
    if not rows:
        return None
    d = pd.DataFrame({"time": pd.to_datetime([int(r["fundingRateTimestamp"]) for r in rows], unit="ms"),
                      "funding_rate": [float(r["fundingRate"]) for r in rows]})
    return d.drop_duplicates("time").set_index("time").sort_index()


KLINES = {"binance": binance_klines, "bybit": bybit_klines}
FUNDING = {"binance": binance_funding, "bybit": bybit_funding}


def funding_ann(f):
    """Средний фандинг в % годовых по фактическим выплатам (интервал у Bybit бывает 8/4/1 ч)."""
    if f is None or len(f) < 2:
        return 0.0
    years = (f.index[-1] - f.index[0]).total_seconds() / (365.25 * 86400)
    return f["funding_rate"].sum() / years * 100 if years > 0 else 0.0


def fetch_crypto(exchange, symbols, frm):
    start0 = int(pd.Timestamp(frm).timestamp() * 1000)
    d_out = f"crypto_data/daily_{exchange}"
    os.makedirs(d_out, exist_ok=True)
    failed = []
    for sym in symbols:
        p_k, p_f = f"{d_out}/{sym}.csv", f"{d_out}/funding_{sym}.csv"
        old_k, old_f = read_cached(p_k), read_cached(p_f)
        s_k = start0 if old_k is None else int(old_k.index[-1].timestamp() * 1000) + DAY_MS
        s_f = start0 if old_f is None else int(old_f.index[-1].timestamp() * 1000) + 1
        try:
            new_k = KLINES[exchange](sym, s_k)
            new_f = FUNDING[exchange](sym, s_f)
        except RuntimeError as e:
            if "400" in str(e) or "10001" in str(e):      # такого символа на бирже нет
                print(f"  {sym}: нет на {exchange} ({e}) — пропуск")
            else:
                print(f"  {sym}: НЕ СКАЧАН ({e})")
                failed.append(sym)
            continue
        if new_k is None and old_k is None:
            print(f"  {sym}: нет дневок (символа нет на бирже?) — пропуск")
            failed.append(sym)
            continue
        k = merge_save(old_k, new_k, p_k) if new_k is not None else old_k
        f = merge_save(old_f, new_f, p_f) if new_f is not None else old_f
        nf = 0 if f is None else len(f)
        f_ann = funding_ann(f)
        print(f"  {sym}: дневок {len(k)} ({k.index[0].date()} – {k.index[-1].date()}, "
              f"новых {0 if new_k is None else len(new_k)}), фандинг-выплат {nf}, "
              f"средний фандинг {f_ann:+.1f}% годовых")
        time.sleep(0.2)
    if failed:
        print(f"  !! не скачались: {', '.join(failed)} — перезапустите ту же команду")
    print(f"Дальше: .venv/bin/python trend_daily.py --source crypto --exchange {exchange}")


# ------------------------------------------------------------------- MOEX

def iss_history(sec, frm, till, pause=0.3):
    """Вся дневная история контракта за окно (страницы по 100 строк)."""
    rows, cols, start, total = [], None, 0, None
    while True:
        js = get_json(ISS_HIST.format(sec=sec, frm=frm, till=till, start=start))
        block = js.get("history", {})
        cols = block.get("columns", cols)
        data = block.get("data", [])
        cur = js.get("history.cursor", {}).get("data") or []
        if cur:
            total = cur[0][1]
        if not data:
            break
        rows.extend(data)
        start += len(data)
        if total is not None and start >= total:
            break
        time.sleep(pause)
    if not rows:
        return None
    d = pd.DataFrame(rows, columns=cols).rename(columns=str.lower)
    d = d.rename(columns={"tradedate": "time", "openposition": "oi", "numtrades": "n_trades"})
    d["time"] = pd.to_datetime(d["time"])
    # CLOSE в ISS бывает пустым в неликвидные дни — тогда берём расчётную цену.
    d["close"] = d["close"].where(d["close"].notna() & (d["close"] > 0), d["settleprice"])
    d = d[d["close"].notna() & (d["close"] > 0)]
    keep = ["time", "secid", "open", "high", "low", "close", "settleprice", "volume", "oi", "n_trades"]
    return d[keep].drop_duplicates("time").set_index("time").sort_index()


def contract_windows(fam, y0, y1):
    """[(код, год_экспирации, from, till)] — окно 13 месяцев до месяца экспирации включительно."""
    out = []
    for y in range(y0, y1 + 1):
        for m in MOEX_FAMILIES[fam]:
            mo = MONTH_OF[m]
            exp_last = pd.Timestamp(y, mo, calendar.monthrange(y, mo)[1])
            frm = (pd.Timestamp(y, mo, 1) - pd.DateOffset(months=13)).strftime("%Y-%m-%d")
            out.append((f"{fam}{m}{y % 10}", y, frm, exp_last.strftime("%Y-%m-%d")))
    return out


def fetch_contract(code, y, frm, till, done, path):
    """Один контракт: (сообщение, есть_ли_данные, ошибка)."""
    try:
        d = iss_history(code, frm, till, pause=0.15)
    except RuntimeError as e:
        return f"    {code}_{y}: НЕ СКАЧАН ({e})", False, True
    if d is None or d.empty:
        # Контракт не торговался (нет в истории) — помечаем пустым файлом,
        # чтобы не дёргать ISS повторно; в сборку пустые не попадают.
        if done:
            save_atomic(pd.DataFrame(columns=["secid", "close"]).rename_axis("time"), path)
        return None, False, False
    save_atomic(d, path)
    return (f"    {code}_{y}: {len(d)} дней, {d.index[0].date()} – {d.index[-1].date()}, "
            f"объём max {int(d['volume'].max())}"), True, False


def fetch_moex(families, y0, y1, workers=4):
    """Контракты семейства качаются в workers потоков (ISS это переносит спокойно),
    каждый контракт — по-прежнему целиком или никак."""
    from concurrent.futures import ThreadPoolExecutor
    today = pd.Timestamp.today().normalize()
    for fam in families:
        print(f"{fam}: контракты {y0}-{y1}", flush=True)
        failed, got, jobs = [], 0, []
        for code, y, frm, till in contract_windows(fam, y0, y1):
            path = f"moex_data/daily/{code}_{y}.csv"
            done = pd.Timestamp(till) < today - pd.Timedelta(days=3)
            if done and os.path.exists(path):
                got += read_cached(path) is not None
                continue
            jobs.append((code, y, frm, till, done, path))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for job, (msg, ok, err) in zip(jobs, ex.map(lambda j: fetch_contract(*j), jobs)):
                if msg:
                    print(msg, flush=True)
                got += ok
                if err:
                    failed.append(f"{job[0]}_{job[1]}")
        print(f"  {fam}: контрактов с данными {got}" + (f", не скачались: {', '.join(failed)}" if failed else ""),
              flush=True)
        rebuild_family(fam, y0)


def rebuild_family(fam, y0=2019):
    """Непрерывный фронт: держим контракт, который накануне был максимален по объёму.
    Контракты с годом экспирации раньше y0 не берём (см. коллизию кодов в probe)."""
    parts = []
    for f in sorted(glob.glob(f"moex_data/daily/{fam}[A-Z][0-9]_[0-9][0-9][0-9][0-9].csv")):
        if int(f[-8:-4]) < y0:
            continue
        d = read_cached(f)
        if d is None or "volume" not in d.columns:
            continue
        d = d[["close", "volume", "oi"]].copy()
        d["code"] = os.path.basename(f)[:-4]
        parts.append(d)
    if not parts:
        print(f"  {fam}: нет файлов контрактов")
        return
    allp = pd.concat(parts)
    vol = allp.pivot_table(index=allp.index, columns="code", values="volume").fillna(0)
    close = allp.pivot_table(index=allp.index, columns="code", values="close")
    oi = allp.pivot_table(index=allp.index, columns="code", values="oi")
    vol = vol[vol.sum(axis=1) > 0]
    front = vol.idxmax(axis=1)                       # фронт дня по объёму
    held = front.shift(1)                            # держим то, что было фронтом накануне
    rows = []
    for t in vol.index[1:]:
        c = held.loc[t]
        prev_t = vol.index[vol.index.get_loc(t) - 1]
        p1, p0 = close.at[t, c], close.at[prev_t, c]
        ret = p1 / p0 - 1 if pd.notna(p1) and pd.notna(p0) and p0 > 0 else float("nan")
        rows.append((t, c, p1, vol.at[t, c], oi.at[t, c], ret))
    out = pd.DataFrame(rows, columns=["time", "code", "close", "volume", "oi", "ret"]).set_index("time")
    # Дни, где «фронт» — дальняя серия с копеечным объёмом (настоящий фронт в ISS не
    # отдан из-за коллизии кодов, см. probe), выбрасываем: порог 2% медианного объёма фронта.
    thr = 0.02 * out["volume"].median()
    fake = out["volume"] < thr
    out = out[~fake].copy()
    gap_after = pd.Series(out.index).diff().dt.days.values > 7
    out.loc[gap_after, "ret"] = float("nan")          # первый день после дыры — без доходности
    save_atomic(out, f"moex_data/daily/cont_{fam}.csv")
    rolls = out["code"][out["code"] != out["code"].shift()]
    print(f"  cont_{fam}.csv: {len(out)} дней, {out.index[0].date()} – {out.index[-1].date()}, "
          f"роллов {len(rolls) - 1}, выброшено дней с фиктивным фронтом (объём < {thr:.0f}): {int(fake.sum())}, "
          f"ret без значения: {int(out['ret'].isna().sum())}, пропусков >7 дней: {int(gap_after.sum())}")


# ------------------------------------------------------------------ probe

def probe():
    t0 = int(pd.Timestamp("2019-01-01").timestamp() * 1000)
    for ex in ("bybit", "binance"):
        print(f"{ex}:")
        try:
            k = KLINES[ex]("BTCUSDT", t0)
            f = FUNDING[ex]("BTCUSDT", t0)
            print(f"  BTCUSDT дневок {len(k)}: {k.index[0].date()} – {k.index[-1].date()}, "
                  f"последний close {k['close'].iloc[-1]:.1f}")
            print(f"  фандинг-выплат {len(f)}: {f.index[0]} – {f.index[-1]}, "
                  f"средний {funding_ann(f):+.1f}% годовых, последний {f['funding_rate'].iloc[-1] * 1e4:+.2f} bp")
        except RuntimeError as e:
            print(f"  ошибка: {e}")
    print("MOEX ISS (глубина истории по истёкшим контрактам; коды *6/*7/*8 заняты контрактами "
          "2026-2028, по ним ISS отдаёт пусто или обрывок — надёжно с SiH9, т.е. с 12.2018):")
    for code, y in (("SiZ8", 2018), ("SiH9", 2019), ("RIH9", 2019), ("BRF9", 2019), ("SiZ6", 2026)):
        _, _, frm, till = [w for w in contract_windows(code[:2], y, y) if w[0] == code][0]
        try:
            d = iss_history(code, frm, till)
        except RuntimeError as e:
            print(f"  {code}_{y}: ошибка {e}")
            continue
        if d is None or d.empty:
            print(f"  {code}_{y}: пусто")
            continue
        print(f"  {code}_{y}: {len(d)} дней, {d.index[0].date()} – {d.index[-1].date()}, "
              f"close {d['close'].min():.0f}-{d['close'].max():.0f}, объём max {int(d['volume'].max())}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--bybit", action="store_true", help="перпы Bybit (торгуемая площадка)")
    ap.add_argument("--binance", action="store_true", help="перпы Binance (для сверки)")
    ap.add_argument("--moex", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="только пересобрать cont_* из скачанного")
    ap.add_argument("--symbols", default=",".join(CRYPTO_SYMBOLS))
    ap.add_argument("--families", default=",".join(MOEX_FAMILIES))
    ap.add_argument("--from", dest="frm", default="2019",
                    help="год экспирации первого контракта (MOEX; раньше 2019 ISS отдаёт мусор из-за коллизии "
                         "кодов) / дата (крипта, по умолчанию 2019-01-01)")
    a = ap.parse_args()
    if a.probe:
        probe()
        return
    for ex, flag in (("bybit", a.bybit), ("binance", a.binance)):
        if flag:
            frm = a.frm if "-" in a.frm else "2019-01-01"
            fetch_crypto(ex, [s.strip().upper() for s in a.symbols.split(",") if s.strip()], frm)
    if a.moex or a.rebuild:
        fams = [f.strip() for f in a.families.split(",") if f.strip()]
        bad = [f for f in fams if f not in MOEX_FAMILIES]
        if bad:
            print(f"неизвестные семейства: {bad}; известные: {list(MOEX_FAMILIES)}")
            return
        os.makedirs("moex_data/daily", exist_ok=True)
        if a.rebuild:
            for fam in fams:
                rebuild_family(fam, int(a.frm[:4]))
        else:
            fetch_moex(fams, int(a.frm[:4]), pd.Timestamp.today().year)
        print("Дальше: .venv/bin/python trend_daily.py --source moex")
    if not (a.binance or a.moex or a.rebuild):
        ap.print_help()


if __name__ == "__main__":
    main()
