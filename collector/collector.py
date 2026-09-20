"""Коллектор Binance USDT-M futures -> ClickHouse.

Публичные стримы (API-ключи НЕ нужны):
  <sym>@depth20@100ms — стакан, топ-20 уровней (пишем раз в SNAPSHOT_MS)
  <sym>@aggTrade      — агрегированные сделки (пишем все)
  <sym>@markPrice@1s  — mark price + funding rate
  <sym>@forceOrder    — ликвидации (принудительные ордера биржи)
  REST /fapi/v1/openInterest — открытый интерес, опрос раз в OI_POLL_SEC

Таблицы (аналог quik.glass / anonymous_transactions из FORTS-архива):
  crypto.glass(ts, symbol, side, price, quantity, level)
  crypto.trades(ts, symbol, price, qty, is_buyer_maker)
  crypto.mark(ts, symbol, mark_price, funding_rate)
  crypto.liquidations(ts, symbol, side, price, qty)
  crypto.open_interest(ts, symbol, open_interest)

Ликвидации и OI добавлены заранее под план "ждать каскад ликвидаций"
(см. FINDINGS.md, п.7) — без них паника прошла бы мимо коллектора.

Батч-вставка раз в FLUSH_SEC секунд, авто-реконнект, лог в stdout.
"""
import asyncio
import json
import logging
import os
import time

import requests
import websockets

SYMBOLS = os.environ.get("SYMBOLS", "btcusdt,ethusdt").lower().split(",")
CH_URL = os.environ.get("CH_URL", "http://clickhouse:8123")
SNAPSHOT_MS = int(os.environ.get("SNAPSHOT_MS", "1000"))
FLUSH_SEC = float(os.environ.get("FLUSH_SEC", "5"))
OI_POLL_SEC = float(os.environ.get("OI_POLL_SEC", "60"))
REST_BASE = "https://fapi.binance.com"
# С 2026-04-23 Binance развёл стримы по эндпоинтам:
#   /public — стакан (depth), /market — сделки/markPrice/forceOrder
WS_PUBLIC = "wss://fstream.binance.com/public/stream?streams="
WS_MARKET = "wss://fstream.binance.com/market/stream?streams="

log = logging.getLogger("collector")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

DDL = [
    "CREATE DATABASE IF NOT EXISTS crypto",
    """CREATE TABLE IF NOT EXISTS crypto.glass (
        ts DateTime64(3, 'UTC'), symbol LowCardinality(String),
        side Enum8('BID' = 1, 'OFFER' = 2),
        price Float64, quantity Float64, level UInt8
    ) ENGINE = MergeTree PARTITION BY toDate(ts) ORDER BY (symbol, ts, side, level)""",
    """CREATE TABLE IF NOT EXISTS crypto.trades (
        ts DateTime64(3, 'UTC'), symbol LowCardinality(String),
        price Float64, qty Float64, is_buyer_maker UInt8
    ) ENGINE = MergeTree PARTITION BY toDate(ts) ORDER BY (symbol, ts)""",
    """CREATE TABLE IF NOT EXISTS crypto.mark (
        ts DateTime64(3, 'UTC'), symbol LowCardinality(String),
        mark_price Float64, funding_rate Float64
    ) ENGINE = MergeTree PARTITION BY toDate(ts) ORDER BY (symbol, ts)""",
    """CREATE TABLE IF NOT EXISTS crypto.liquidations (
        ts DateTime64(3, 'UTC'), symbol LowCardinality(String),
        side Enum8('BUY' = 1, 'SELL' = 2),
        price Float64, qty Float64
    ) ENGINE = MergeTree PARTITION BY toDate(ts) ORDER BY (symbol, ts)""",
    """CREATE TABLE IF NOT EXISTS crypto.open_interest (
        ts DateTime64(3, 'UTC'), symbol LowCardinality(String),
        open_interest Float64
    ) ENGINE = MergeTree PARTITION BY toDate(ts) ORDER BY (symbol, ts)""",
]

buffers = {"glass": [], "trades": [], "mark": [], "liquidations": [],
           "open_interest": []}
last_snapshot_ms = {}
stats = {"glass": 0, "trades": 0, "mark": 0, "liquidations": 0,
         "open_interest": 0, "unknown": 0}


def ch_execute(query: str, body: str = "") -> None:
    r = requests.post(CH_URL, params={"query": query},
                      data=body.encode(), timeout=30)
    r.raise_for_status()


def init_tables() -> None:
    for ddl in DDL:
        ch_execute(ddl)
    log.info("Таблицы готовы: %s", CH_URL)


def flush() -> None:
    for table, rows in buffers.items():
        if not rows:
            continue
        body = "\n".join(json.dumps(r) for r in rows)
        try:
            ch_execute(f"INSERT INTO crypto.{table} FORMAT JSONEachRow", body)
            stats[table] += len(rows)
            rows.clear()
        except Exception as e:  # не теряем буфер при сбое CH
            log.error("flush %s: %s (буфер %d строк)", table, e, len(rows))
            if len(rows) > 500_000:  # защита памяти
                del rows[:250_000]


def ts_iso(ms: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.gmtime(ms / 1000)) + f".{ms % 1000:03d}"


def on_message(stream: str, d: dict) -> None:
    """Диспетчеризация по полю события 'e' (регистронезависимо) с фолбэком
    на имя стрима: Binance в combined-режиме может менять регистр имён."""
    sym = (d.get("s") or stream.split("@")[0]).upper()
    event = str(d.get("e", "")).lower()
    s_low = stream.lower()
    if event == "aggtrade" or "@aggtrade" in s_low:
        buffers["trades"].append(
            {"ts": ts_iso(d["T"]), "symbol": sym, "price": float(d["p"]),
             "qty": float(d["q"]), "is_buyer_maker": int(d["m"])})
    elif event == "markpriceupdate" or "@markprice" in s_low:
        buffers["mark"].append(
            {"ts": ts_iso(d["E"]), "symbol": sym,
             "mark_price": float(d["p"]),
             "funding_rate": float(d["r"] or 0)})
    elif event == "depthupdate" or "@depth20" in s_low:
        now_ms = d["E"]
        if now_ms - last_snapshot_ms.get(sym, 0) < SNAPSHOT_MS:
            return
        last_snapshot_ms[sym] = now_ms
        ts = ts_iso(d["T"])  # transaction time
        for side, key in (("BID", "b"), ("OFFER", "a")):
            for lvl, (price, qty) in enumerate(d[key]):
                buffers["glass"].append(
                    {"ts": ts, "symbol": sym, "side": side,
                     "price": float(price), "quantity": float(qty),
                     "level": lvl})
    elif event == "forceorder" or "@forceorder" in s_low:
        # Ликвидация: полезная нагрузка вложена в поле "o".
        o = d.get("o", {})
        buffers["liquidations"].append(
            {"ts": ts_iso(o.get("T", d.get("E"))),
             "symbol": (o.get("s") or sym),
             "side": o.get("S", "BUY"),
             "price": float(o.get("ap") or o.get("p") or 0),
             "qty": float(o.get("z") or o.get("q") or 0)})
    else:
        stats["unknown"] += 1


async def flusher() -> None:
    last_report = time.time()
    while True:
        await asyncio.sleep(FLUSH_SEC)
        await asyncio.get_event_loop().run_in_executor(None, flush)
        if time.time() - last_report > 300:
            log.info("вставлено всего: %s", stats)
            last_report = time.time()


def fetch_oi(sym: str) -> dict | None:
    """Синхронный REST-запрос open interest (нет WS-стрима для OI)."""
    try:
        r = requests.get(f"{REST_BASE}/fapi/v1/openInterest",
                         params={"symbol": sym.upper()}, timeout=10)
        r.raise_for_status()
        d = r.json()
        return {"ts": ts_iso(int(time.time() * 1000)), "symbol": sym.upper(),
                "open_interest": float(d["openInterest"])}
    except Exception as e:
        log.warning("OI %s: %s", sym, e)
        return None


async def oi_poller() -> None:
    loop = asyncio.get_event_loop()
    while True:
        for sym in SYMBOLS:
            row = await loop.run_in_executor(None, fetch_oi, sym)
            if row:
                buffers["open_interest"].append(row)
        await asyncio.sleep(OI_POLL_SEC)


seen_events = set()


async def consume(url: str, label: str) -> None:
    while True:
        try:
            async with websockets.connect(url, ping_interval=180,
                                          max_size=2 ** 22) as ws:
                log.info("подключено [%s]", label)
                async for raw in ws:
                    msg = json.loads(raw)
                    if "result" in msg or "error" in msg:
                        log.info("[%s] ответ биржи: %s", label, msg)
                        continue
                    d = msg.get("data", msg)
                    ev = str(d.get("e", "?"))
                    if ev not in seen_events:
                        seen_events.add(ev)
                        log.info("[%s] первое событие типа: %s", label, ev)
                    on_message(msg.get("stream", ""), d)
        except Exception as e:
            log.warning("[%s] reconnect через 5с: %s", label, e)
            await asyncio.sleep(5)


async def main() -> None:
    for attempt in range(60):  # ждём ClickHouse
        try:
            init_tables()
            break
        except Exception as e:
            log.info("жду ClickHouse (%s)...", e)
            await asyncio.sleep(5)
    public_url = WS_PUBLIC + "/".join(
        f"{s}@depth20@100ms" for s in SYMBOLS)
    market_url = WS_MARKET + "/".join(
        f"{s}@{ch}" for s in SYMBOLS
        for ch in ("aggTrade", "markPrice@1s", "forceOrder"))
    await asyncio.gather(consume(public_url, "public"),
                         consume(market_url, "market"),
                         flusher(),
                         oi_poller())


if __name__ == "__main__":
    asyncio.run(main())
