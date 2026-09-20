"""Коллектор котировок US-индексов «с крипты» -> ClickHouse crypto.index_quotes.

Идея: проводник (S&P 500 / Nasdaq-100) берём из крипто-инфраструктуры —
токенизированные ETF (xStocks: SPYx, QQQx) на крипто-биржах с публичными
websocket без ключей, — а рядом пишем эталон реального SPY (Yahoo, опрос раз в
15 с) и, если есть ключ, оракул Pyth. Несколько источников в одной таблице
с колонкой source: потом видно, какой из них ведёт, какой отстаёт и насколько
токен отклоняется от ETF.

Источники (env INDEX_SOURCES, через запятую):
  kraken — wss://ws.kraken.com/v2, канал ticker (bid/ask/last), пары xStocks
           вида SPYx/USD; список пар проверяется через REST AssetPairs
  bybit  — wss://stream.bybit.com/v5/public/spot, orderbook.1 (лучшие bid/ask),
           символы вида SPYXUSDT; проверка через instruments-info
  gate   — wss://api.gateio.ws/ws/v4/, spot.book_ticker, пары вида SPYX_USDT
  yahoo  — https://query1.finance.yahoo.com/v8/finance/chart/<SYM>, опрос
           раз в YAHOO_POLL_SEC; regularMarketPrice + regularMarketTime
  hermes — Pyth Hermes SSE; нужен HERMES_API_KEY (без ключа сейчас 401)

Символы по источникам — env KRAKEN_SYMBOLS / BYBIT_SYMBOLS / GATE_SYMBOLS /
YAHOO_SYMBOLS / HERMES_SYMBOLS (значения по умолчанию ниже). Имена в таблице
нормализуются: SPYx/USD, SPYXUSDT, SPYX_USDT, Equity.US.SPY/USD -> SPY.

Строка: ts (время биржи, если источник его даёт, иначе время приёма),
recv_ts (время приёма), source, symbol, price (mid по bid/ask, либо last),
conf (спред ask-bid, у Pyth — conf), bid, ask.
Пишем при изменении цены и не реже раза в 30 с (heartbeat).

Режимы:  --probe   разведка с сервера: какие источники/символы отвечают
         (ничего не пишет в базу);
         без флагов — сбор.
"""
import asyncio
import json
import logging
import os
import sys
import time

import requests

try:
    import websockets
except ImportError:  # только --probe по REST без ws
    websockets = None

CH_URL = os.environ.get("CH_URL", "http://clickhouse:8123")
FLUSH_SEC = float(os.environ.get("FLUSH_SEC", "2"))
HEARTBEAT_SEC = float(os.environ.get("HEARTBEAT_SEC", "30"))
SOURCES = [s.strip().lower() for s in os.environ.get(
    "INDEX_SOURCES", "kraken,bybit,gate,yahoo,hermes").split(",") if s.strip()]

KRAKEN_SYMBOLS = [s.strip() for s in os.environ.get(
    "KRAKEN_SYMBOLS", "SPYx/USD,QQQx/USD,BTC/USD,ETH/USD").split(",") if s.strip()]
# Пары, для которых ticker подписывается с event_trigger='bbo' (обновление на
# смену лучшего бида/аска). По умолчанию Kraken шлёт ticker ТОЛЬКО на сделку,
# а у xStocks сделок ~200/сутки при постоянно двигающейся котировке.
KRAKEN_BBO_SYMBOLS = [s.strip() for s in os.environ.get(
    "KRAKEN_BBO_SYMBOLS", "SPYx/USD,QQQx/USD").split(",") if s.strip()]
BYBIT_SYMBOLS = [s.strip() for s in os.environ.get(
    "BYBIT_SYMBOLS", "SPYXUSDT,QQQXUSDT,BTCUSDT").split(",") if s.strip()]
GATE_SYMBOLS = [s.strip() for s in os.environ.get(
    "GATE_SYMBOLS", "SPYX_USDT,QQQX_USDT,BTC_USDT").split(",") if s.strip()]
YAHOO_SYMBOLS = [s.strip() for s in os.environ.get(
    "YAHOO_SYMBOLS", "SPY,QQQ").split(",") if s.strip()]
YAHOO_POLL_SEC = float(os.environ.get("YAHOO_POLL_SEC", "15"))
HERMES = os.environ.get("HERMES_URL", "https://hermes.pyth.network").rstrip("/")
HERMES_API_KEY = os.environ.get("HERMES_API_KEY", "").strip()
HERMES_SYMBOLS = [s.strip() for s in os.environ.get(
    "HERMES_SYMBOLS", "Equity.US.SPY/USD,Equity.US.QQQ/USD,Crypto.BTC/USD,Crypto.ETH/USD"
).split(",") if s.strip()]
KNOWN_PYTH_IDS = {
    "Crypto.BTC/USD": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "Crypto.ETH/USD": "ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "Equity.US.SPY/USD": "19e09bb805456ada3979a7d1cbb4b6d63babc3a0f8e8a9509f68afa5c4c11cd5",
    "Equity.US.QQQ/USD": "9695e2b96ea7b3859da9ed25b7a46a920a776e2fdae19a7bcfdf2b219230452d",
    "Metal.XAU/USD": "765d2ba906dbc32ca17cc11f5310a89e9ee1f6420508c63861f2f8ba4ee34bb2",
}
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

log = logging.getLogger("index_collector")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

DDL = [
    "CREATE DATABASE IF NOT EXISTS crypto",
    """CREATE TABLE IF NOT EXISTS crypto.index_quotes (
        ts DateTime64(3, 'UTC'), recv_ts DateTime64(3, 'UTC'),
        source LowCardinality(String), symbol LowCardinality(String),
        price Float64, conf Float64, bid Float64, ask Float64
    ) ENGINE = MergeTree PARTITION BY toDate(ts) ORDER BY (source, symbol, ts)""",
    "ALTER TABLE crypto.index_quotes ADD COLUMN IF NOT EXISTS bid Float64",
    "ALTER TABLE crypto.index_quotes ADD COLUMN IF NOT EXISTS ask Float64",
]

buffer = []
stats = {"rows": 0, "msgs": 0, "dup": 0, "unknown": 0}
last_row = {}      # (source, symbol) -> (price, wall_time)


# ------------------------------------------------------------------ utils
def canon(sym: str) -> str:
    """SPYx/USD, SPYXUSDT, SPYX_USDT, Equity.US.SPY/USD -> SPY; BTC/USD -> BTC."""
    s = sym.split("/")[0].split("_")[0]
    s = s.split(".")[-1]
    for q in ("USDT", "USDC", "USD"):
        if s.upper().endswith(q) and len(s) > len(q) + 1:
            s = s[: -len(q)]
            break
    if len(s) >= 3 and s[-1] in "xX" and s[:-1].isalpha() and s[:-1].isupper():
        s = s[:-1]                         # токенизированный тикер xStocks
    return s.upper()


def ts_iso(sec: float) -> str:
    ms = int(round(sec * 1000))
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ms / 1000)) + f".{ms % 1000:03d}"


def ch_execute(query: str, body: str = "") -> None:
    r = requests.post(CH_URL, params={"query": query}, data=body.encode(), timeout=30)
    r.raise_for_status()


def init_tables() -> None:
    for ddl in DDL:
        ch_execute(ddl)
    log.info("Таблица crypto.index_quotes готова: %s", CH_URL)


def flush() -> None:
    if not buffer:
        return
    body = "\n".join(json.dumps(r) for r in buffer)
    try:
        ch_execute("INSERT INTO crypto.index_quotes FORMAT JSONEachRow", body)
        stats["rows"] += len(buffer)
        buffer.clear()
    except Exception as e:
        log.error("flush: %s (буфер %d строк)", e, len(buffer))
        if len(buffer) > 200_000:
            del buffer[:100_000]


def emit(source: str, symbol: str, price: float, conf: float = 0.0,
         bid: float = 0.0, ask: float = 0.0, ts: float = None) -> None:
    """Записать котировку, если цена изменилась или прошло HEARTBEAT_SEC."""
    stats["msgs"] += 1
    if not price or price <= 0:
        stats["unknown"] += 1
        return
    now = time.time()
    key = (source, symbol)
    prev = last_row.get(key)
    if prev is not None and prev[0] == price and now - prev[1] < HEARTBEAT_SEC:
        stats["dup"] += 1
        return
    last_row[key] = (price, now)
    buffer.append({"ts": ts_iso(ts if ts else now), "recv_ts": ts_iso(now),
                   "source": source, "symbol": symbol, "price": price,
                   "conf": conf, "bid": bid, "ask": ask})


async def flusher() -> None:
    last_report = time.time()
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(FLUSH_SEC)
        await loop.run_in_executor(None, flush)
        if time.time() - last_report > 300:
            log.info("stats: %s, буфер %d", stats, len(buffer))
            last_report = time.time()


# ------------------------------------------------------------------ REST-разведка
def kraken_pairs(query=("SPY", "QQQ", "BTC/USD", "ETH/USD")):
    """Только информационно: классический AssetPairs — это спотовый матчинг-энджин
    крипты и НЕ перечисляет xStocks (токенизированные акции/ETF, отдельная
    продуктовая линейка). Поэтому пустой результат для SPY/QQQ тут — норма;
    доказательство работоспособности — это OK в ws-пробе ниже (проверено
    09.09.2026: kraken SPY=763.9 / QQQ=717.4, совпало с реальным SPY/QQQ)."""
    r = requests.get("https://api.kraken.com/0/public/AssetPairs", timeout=20, headers=UA)
    r.raise_for_status()
    out = {}
    for key, v in r.json().get("result", {}).items():
        ws = v.get("wsname", "")
        if any(q.upper() in ws.upper() for q in query):
            out[ws] = key
    return out


def bybit_symbols(query=("SPY", "QQQ")):
    found = []
    cursor = ""
    for _ in range(10):
        r = requests.get("https://api.bybit.com/v5/market/instruments-info",
                         params={"category": "spot", "limit": 1000, "cursor": cursor},
                         timeout=20, headers=UA)
        r.raise_for_status()
        res = r.json().get("result", {})
        for it in res.get("list", []):
            s = it.get("symbol", "")
            if any(q in s for q in query) or s in ("BTCUSDT", "ETHUSDT"):
                found.append((s, it.get("status")))
        cursor = res.get("nextPageCursor") or ""
        if not cursor:
            break
    return found


def gate_pairs(query=("SPY", "QQQ")):
    r = requests.get("https://api.gateio.ws/api/v4/spot/currency_pairs", timeout=20, headers=UA)
    r.raise_for_status()
    return [(p["id"], p.get("trade_status")) for p in r.json()
            if any(q in p["id"] for q in query) or p["id"] in ("BTC_USDT", "ETH_USDT")]


YAHOO_HEADERS = dict(UA)
YAHOO_HEADERS.update({
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
})
_yahoo_session = requests.Session()
_yahoo_session.headers.update(YAHOO_HEADERS)


def yahoo_quote(sym: str):
    """query2 первым (обычно меньше троттлится), query1 — запасной; сессия
    с keep-alive и полным набором браузерных заголовков против 429."""
    last_exc = None
    for host in ("query2", "query1"):
        try:
            r = _yahoo_session.get(
                f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"interval": "1m", "range": "1d", "includePrePost": "true"},
                timeout=20)
            if r.status_code == 429:
                last_exc = RuntimeError(f"HTTP 429 ({host})")
                continue
            r.raise_for_status()
            meta = r.json()["chart"]["result"][0]["meta"]
            return float(meta["regularMarketPrice"]), int(meta["regularMarketTime"])
        except Exception as e:
            last_exc = e
    raise last_exc


def hermes_headers():
    h = {"Accept": "application/json"}
    if HERMES_API_KEY:
        h["Authorization"] = f"Bearer {HERMES_API_KEY}"
    return h


def hermes_resolve(symbols):
    ids = {}
    for sym in symbols:
        base = canon(sym)
        found = None
        try:
            r = requests.get(f"{HERMES}/v2/price_feeds", params={"query": base},
                             timeout=20, headers=hermes_headers())
            r.raise_for_status()
            for feed in r.json():
                if feed.get("attributes", {}).get("symbol", "").lower() == sym.lower():
                    found = feed["id"].lower().replace("0x", "")
                    break
        except Exception as e:
            log.warning("hermes resolve %s: %s", sym, e)
        found = found or KNOWN_PYTH_IDS.get(sym)
        if found:
            ids[sym] = found
    return ids


# ------------------------------------------------------------------ источники
async def src_kraken(symbols):
    """Kraken WS v2, канал ticker: bid/ask/last без времени биржи -> ts = приём.

    event_trigger по умолчанию 'trades': тикер приходит только на сделку. Для
    ликвидных BTC/ETH это нормально, для xStocks — нет (SPYx дал 1661 строку за
    9 суток против 442k у gate book_ticker). Поэтому пары из KRAKEN_BBO_SYMBOLS
    подписываем отдельным сообщением с event_trigger='bbo'.
    """
    url = "wss://ws.kraken.com/v2"
    bbo = [s for s in symbols if s in KRAKEN_BBO_SYMBOLS]
    trades = [s for s in symbols if s not in KRAKEN_BBO_SYMBOLS]
    subs = []
    if bbo:
        subs.append({"method": "subscribe", "params": {
            "channel": "ticker", "symbol": bbo, "event_trigger": "bbo"}})
    if trades:
        subs.append({"method": "subscribe", "params": {
            "channel": "ticker", "symbol": trades}})
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, max_size=2 ** 22) as ws:
                for sub in subs:
                    await ws.send(json.dumps(sub))
                log.info("[kraken] подключено, bbo=%s trades=%s", bbo, trades)
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("method") == "subscribe" and not msg.get("success", True):
                        log.error("[kraken] подписка отклонена: %s", msg)
                    if msg.get("channel") != "ticker":
                        continue
                    for d in msg.get("data", []):
                        bid, ask = float(d.get("bid") or 0), float(d.get("ask") or 0)
                        last = float(d.get("last") or 0)
                        mid = (bid + ask) / 2 if bid and ask else last
                        emit("kraken", canon(d["symbol"]), mid, ask - bid if bid and ask else 0.0,
                             bid, ask)
        except Exception as e:
            log.warning("[kraken] reconnect через 10с: %s", e)
            await asyncio.sleep(10)


async def src_bybit(symbols):
    """Bybit v5 spot, orderbook.1: лучшие bid/ask, ts биржи в мс."""
    url = "wss://stream.bybit.com/v5/public/spot"
    args = [f"orderbook.1.{s}" for s in symbols]
    book = {}
    while True:
        try:
            async with websockets.connect(url, ping_interval=None, max_size=2 ** 22) as ws:
                await ws.send(json.dumps({"op": "subscribe", "args": args}))
                log.info("[bybit] подключено, %s", symbols)

                async def pinger():
                    while True:
                        await asyncio.sleep(20)
                        await ws.send(json.dumps({"op": "ping"}))
                ping_task = asyncio.ensure_future(pinger())
                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("op") == "subscribe" and not msg.get("success", True):
                            log.error("[bybit] подписка отклонена: %s", msg)
                        topic = msg.get("topic", "")
                        if not topic.startswith("orderbook.1."):
                            continue
                        d = msg.get("data", {})
                        s = d.get("s") or topic.split(".")[-1]
                        b = book.setdefault(s, [0.0, 0.0])
                        if d.get("b"):
                            b[0] = float(d["b"][0][0])
                        if d.get("a"):
                            b[1] = float(d["a"][0][0])
                        bid, ask = b
                        if bid and ask:
                            emit("bybit", canon(s), (bid + ask) / 2, ask - bid, bid, ask,
                                 ts=int(msg.get("ts", 0)) / 1000 or None)
                finally:
                    ping_task.cancel()
        except Exception as e:
            log.warning("[bybit] reconnect через 10с: %s", e)
            await asyncio.sleep(10)


async def src_gate(symbols):
    """Gate.io v4 spot, spot.book_ticker: лучшие bid/ask, t в мс."""
    url = "wss://api.gateio.ws/ws/v4/"
    while True:
        try:
            async with websockets.connect(url, ping_interval=None, max_size=2 ** 22) as ws:
                await ws.send(json.dumps({"time": int(time.time()), "channel": "spot.book_ticker",
                                          "event": "subscribe", "payload": symbols}))
                log.info("[gate] подключено, %s", symbols)

                async def pinger():
                    while True:
                        await asyncio.sleep(20)
                        await ws.send(json.dumps({"time": int(time.time()), "channel": "spot.ping"}))
                ping_task = asyncio.ensure_future(pinger())
                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("event") == "subscribe" and msg.get("error"):
                            log.error("[gate] подписка отклонена: %s", msg)
                        if msg.get("channel") != "spot.book_ticker" or msg.get("event") != "update":
                            continue
                        d = msg.get("result", {})
                        bid, ask = float(d.get("b") or 0), float(d.get("a") or 0)
                        if bid and ask:
                            emit("gate", canon(d["s"]), (bid + ask) / 2, ask - bid, bid, ask,
                                 ts=int(d.get("t", 0)) / 1000 or None)
                finally:
                    ping_task.cancel()
        except Exception as e:
            log.warning("[gate] reconnect через 10с: %s", e)
            await asyncio.sleep(10)


async def src_yahoo(symbols):
    """Yahoo chart API: последняя цена и её время, опрос раз в YAHOO_POLL_SEC.
    Бэкофф ступенчатый: после 5 ошибок подряд x3, после 20 x8 — если IP сервера
    просто зафлагован Yahoo (частый случай для дата-центровых адресов), это
    источник best-effort, не блокирующий kraken/gate."""
    loop = asyncio.get_event_loop()
    fails = 0
    while True:
        for sym in symbols:
            try:
                price, t = await loop.run_in_executor(None, yahoo_quote, sym)
                emit("yahoo", canon(sym), price, ts=t)
                fails = 0
            except Exception as e:
                fails += 1
                if fails in (1, 5, 20, 100):
                    log.warning("[yahoo] %s: %s (ошибок подряд %d)", sym, e, fails)
        backoff = 8 if fails > 20 else 3 if fails > 5 else 1
        await asyncio.sleep(YAHOO_POLL_SEC * backoff)


def hermes_stream_blocking(ids: dict) -> None:
    id_to_sym = {v: k for k, v in ids.items()}
    params = [("ids[]", "0x" + v) for v in ids.values()] + [
        ("parsed", "true"), ("encoding", "hex"), ("allow_unordered", "true"),
        ("ignore_invalid_price_ids", "true")]
    if HERMES_API_KEY:
        params.append(("apikey", HERMES_API_KEY))
    headers = hermes_headers(); headers["Accept"] = "text/event-stream"
    last_pub = {}
    while True:
        try:
            with requests.get(f"{HERMES}/v2/updates/price/stream", params=params,
                              stream=True, timeout=(15, 90), headers=headers) as r:
                if r.status_code == 401:
                    log.error("[hermes] 401 unauthorized — нужен HERMES_API_KEY; источник остановлен")
                    return
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.raw.read(200)!r}")
                log.info("[hermes] подключено, фидов %d", len(ids))
                for line in r.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        payload = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    for p in payload.get("parsed", []):
                        fid = str(p.get("id", "")).lower().replace("0x", "")
                        sym = id_to_sym.get(fid)
                        pr = p.get("price") or {}
                        if sym is None or "price" not in pr:
                            continue
                        expo = int(pr["expo"])
                        price = int(pr["price"]) * 10.0 ** expo
                        pub = int(pr["publish_time"])
                        if last_pub.get(fid) == pub:
                            continue
                        last_pub[fid] = pub
                        emit("pyth", canon(sym), price, int(pr.get("conf", 0)) * 10.0 ** expo, ts=pub)
        except Exception as e:
            log.warning("[hermes] reconnect через 15с: %s", e)
            time.sleep(15)


async def src_hermes(symbols):
    loop = asyncio.get_event_loop()
    ids = await loop.run_in_executor(None, hermes_resolve, symbols)
    if not ids:
        log.error("[hermes] нет фидов, источник пропущен"); return
    await loop.run_in_executor(None, hermes_stream_blocking, ids)


# ------------------------------------------------------------------ probe
async def probe_ws(name, coro, seconds=15):
    """Запустить источник на seconds секунд и посчитать, что он прислал."""
    before = {k: v for k, v in stats.items()}
    got = {}
    orig_emit = globals()["emit"]

    def counting_emit(source, symbol, price, conf=0.0, bid=0.0, ask=0.0, ts=None):
        got[(source, symbol)] = (price, bid, ask)
    globals()["emit"] = counting_emit
    task = asyncio.ensure_future(coro)
    try:
        await asyncio.sleep(seconds)
    finally:
        task.cancel()
        globals()["emit"] = orig_emit
    if got:
        for (src, sym), (p, b, a) in sorted(got.items()):
            print(f"  OK  {src:<7} {sym:<5} price={p:.4f} bid={b} ask={a}")
    else:
        print(f"  --  {name}: за {seconds} с котировок не пришло (см. лог выше)")


def probe() -> None:
    print("=== Разведка источников (без записи в базу) ===")
    if "kraken" in SOURCES:
        try:
            pairs = kraken_pairs()
            print(f"kraken AssetPairs с SPY/QQQ/BTC/ETH: {sorted(pairs)}")
            if not any("SPY" in p.upper() or "QQQ" in p.upper() for p in pairs):
                print("  (пусто для SPY/QQQ — ожидаемо: xStocks не в AssetPairs, "
                      "смотри ws-пробу ниже, это единственный надёжный источник правды)")
        except Exception as e:
            print(f"kraken REST: {e}")
    if "bybit" in SOURCES:
        try:
            print(f"bybit spot с SPY/QQQ: {bybit_symbols()}")
        except Exception as e:
            print(f"bybit REST: {e}")
    if "gate" in SOURCES:
        try:
            print(f"gate spot с SPY/QQQ: {gate_pairs()}")
        except Exception as e:
            print(f"gate REST: {e}")
    if "yahoo" in SOURCES:
        for sym in YAHOO_SYMBOLS:
            try:
                p, t = yahoo_quote(sym)
                print(f"yahoo {sym}: {p} @ {ts_iso(t)}")
            except Exception as e:
                print(f"yahoo {sym}: {e}")
    if "hermes" in SOURCES:
        ids = hermes_resolve(HERMES_SYMBOLS)
        print(f"hermes id: {len(ids)} из {len(HERMES_SYMBOLS)}; ключ {'задан' if HERMES_API_KEY else 'НЕ задан'}")
        try:
            r = requests.get(f"{HERMES}/v2/updates/price/latest",
                             params=[("ids[]", "0x" + v) for v in ids.values()] + [("parsed", "true")],
                             timeout=20, headers=hermes_headers())
            print(f"hermes latest: HTTP {r.status_code} {r.text[:120]!r}")
        except Exception as e:
            print(f"hermes latest: {e}")
    if websockets is None:
        print("websockets не установлен — ws-источники не проверены"); return

    async def run_ws():
        if "kraken" in SOURCES:
            print("kraken ws (15 с)..."); await probe_ws("kraken", src_kraken(KRAKEN_SYMBOLS))
        if "bybit" in SOURCES:
            print("bybit ws (15 с)..."); await probe_ws("bybit", src_bybit(BYBIT_SYMBOLS))
        if "gate" in SOURCES:
            print("gate ws (15 с)..."); await probe_ws("gate", src_gate(GATE_SYMBOLS))
    asyncio.run(run_ws())
    print("Итог: источники с OK можно оставить в INDEX_SOURCES; символы без OK — "
          "поправить *_SYMBOLS по спискам выше.")


# ------------------------------------------------------------------ main
async def main_async() -> None:
    for attempt in range(60):
        try:
            init_tables(); break
        except Exception as e:
            log.info("жду ClickHouse (%s)...", e); await asyncio.sleep(5)
    tasks = [flusher()]
    if "kraken" in SOURCES and websockets:
        tasks.append(src_kraken(KRAKEN_SYMBOLS))
    if "bybit" in SOURCES and websockets:
        tasks.append(src_bybit(BYBIT_SYMBOLS))
    if "gate" in SOURCES and websockets:
        tasks.append(src_gate(GATE_SYMBOLS))
    if "yahoo" in SOURCES:
        tasks.append(src_yahoo(YAHOO_SYMBOLS))
    if "hermes" in SOURCES:
        tasks.append(src_hermes(HERMES_SYMBOLS))
    log.info("источники: %s", SOURCES)
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    else:
        asyncio.run(main_async())
