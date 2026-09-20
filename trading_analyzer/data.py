"""Загрузка котировок из локальных zip-архивов Stooq без распаковки.

Архивы (d_us_txt.zip, h_world_txt.zip, ...) лежат в stooq_data/.
Внутри: data/<daily|hourly|5 min>/<us|world>/<категория>/<тикер>.txt
Формат строк: <TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>
"""
from __future__ import annotations

import json
import pickle
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

# Префикс имени архива -> таймфрейм
_TF_BY_PREFIX = {"d": "d", "h": "h", "5": "5"}

_COLUMNS = {
    "<DATE>": "Date",
    "<TIME>": "Time",
    "<OPEN>": "Open",
    "<HIGH>": "High",
    "<LOW>": "Low",
    "<CLOSE>": "Close",
    "<VOL>": "Volume",
}


class StooqData:
    """Индексирует архивы Stooq и отдаёт OHLCV-данные по тикеру.

    >>> data = StooqData("stooq_data")
    >>> df = data.load("btc.v", timeframe="d")   # крипта
    >>> df = data.load("aapl.us", timeframe="h") # акции, часовики
    """

    def __init__(self, archive_dir: str = "stooq_data", cache_dir: str = "cache"):
        self.archive_dir = Path(archive_dir)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self._index: Optional[Dict[str, Dict[str, List[str]]]] = None

    # ------------------------------------------------------------------ index
    @property
    def index(self) -> Dict[str, Dict[str, List[str]]]:
        """{timeframe: {ticker: [zip_path, member]}} — строится один раз, кэшируется."""
        if self._index is None:
            self._index = self._load_or_build_index()
        return self._index

    def _index_cache_path(self) -> Path:
        return self.cache_dir / "archive_index.json"

    def _load_or_build_index(self) -> Dict[str, Dict[str, List[str]]]:
        cache = self._index_cache_path()
        zips = sorted(self.archive_dir.glob("*.zip"))
        if not zips:
            raise FileNotFoundError(f"Нет zip-архивов в {self.archive_dir}")
        newest = max(z.stat().st_mtime for z in zips)
        if cache.exists() and cache.stat().st_mtime >= newest:
            with open(cache) as f:
                return json.load(f)

        index: Dict[str, Dict[str, List[str]]] = {}
        for zpath in zips:
            tf = _TF_BY_PREFIX.get(zpath.name[0])
            if tf is None:
                continue
            with zipfile.ZipFile(zpath) as zf:
                for member in zf.namelist():
                    if not member.endswith(".txt"):
                        continue
                    ticker = Path(member).stem.lower()  # 'aapl.us', 'btc.v'
                    index.setdefault(tf, {})[ticker] = [str(zpath), member]
        with open(cache, "w") as f:
            json.dump(index, f)
        return index

    def list_tickers(self, timeframe: str = "d", contains: str = "") -> List[str]:
        """Список доступных тикеров (фильтр по подстроке)."""
        tickers = self.index.get(timeframe, {})
        return sorted(t for t in tickers if contains.lower() in t)

    # ------------------------------------------------------------------- load
    def load(self, ticker: str, timeframe: str = "d",
             use_cache: bool = True) -> pd.DataFrame:
        """OHLCV DataFrame с DatetimeIndex. timeframe: 'd' | 'h' | '5'."""
        ticker = ticker.lower()
        cache = self.cache_dir / f"{ticker}_{timeframe}.pkl"
        if use_cache and cache.exists():
            with open(cache, "rb") as f:
                return pickle.load(f)

        try:
            zpath, member = self.index[timeframe][ticker]
        except KeyError:
            raise KeyError(
                f"Тикер '{ticker}' не найден для таймфрейма '{timeframe}'. "
                f"Поиск: data.list_tickers('{timeframe}', contains='{ticker.split('.')[0]}')"
            )

        with zipfile.ZipFile(zpath) as zf, zf.open(member) as f:
            df = pd.read_csv(f)
        df = df.rename(columns=_COLUMNS)
        dt = pd.to_datetime(
            df["Date"].astype(str) + df["Time"].astype(str).str.zfill(6),
            format="%Y%m%d%H%M%S",
        )
        df = df[["Open", "High", "Low", "Close", "Volume"]].set_index(dt)
        df.index.name = "Date"
        df = df[~df.index.duplicated(keep="last")].sort_index()

        if use_cache:
            with open(cache, "wb") as f:
                pickle.dump(df, f)
        return df

    def load_closes(self, tickers: List[str], timeframe: str = "d") -> pd.DataFrame:
        """Цены Close нескольких инструментов в одном DataFrame (для корреляций)."""
        closes = {}
        for t in tickers:
            try:
                closes[t] = self.load(t, timeframe)["Close"]
            except KeyError as e:
                print(f"  пропуск: {e}")
        return pd.DataFrame(closes)
