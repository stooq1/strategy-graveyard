#!/bin/bash
# Минутки US-индексов с Dukascopy (бесплатно, без ключа, UTC) -> moex_data/.
# Запуск:  bash fetch_us_index.sh [С_ДАТЫ]
set -e
cd "$(dirname "$0")"
FROM=${1:-2023-01-01}
TO=$(date -u +%F)
PY=$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
mkdir -p moex_data .dukascopy

dl () {  # $1 = инструмент dukascopy, $2 = имя в проекте
  if [ -s ".dukascopy/$2.csv" ]; then
    echo "== $2: сырой файл уже есть, качать не надо"
  else
    echo "== $2 ($1) $FROM .. $TO"
    npx --yes dukascopy-node -i "$1" -from "$FROM" -to "$TO" -t m1 -f csv \
        -dir .dukascopy -fn "$2" -v
  fi
  "$PY" - "$2" <<'PY'
import sys, glob, pandas as pd
name = sys.argv[1]
f = sorted(glob.glob(f".dukascopy/{name}*.csv"))[-1]
d = pd.read_csv(f)
c = {x.lower(): x for x in d.columns}
ts = c.get("timestamp") or c.get("time") or d.columns[0]
d["time"] = (pd.to_datetime(d[ts], unit="ms", utc=True)
             if pd.api.types.is_numeric_dtype(d[ts])
             else pd.to_datetime(d[ts], utc=True))
d["time"] = d["time"].dt.tz_localize(None)
cols = ["time"] + [c[k] for k in ("open", "high", "low", "close", "volume") if k in c]
out = d[cols].drop_duplicates("time").sort_values("time")
p = f"moex_data/inv_{name}.csv"
out.to_csv(p, index=False)
print(f"  {p}: {len(out)} минут, {out['time'].iloc[0]} – {out['time'].iloc[-1]}, "
      f"close {out[c['close']].min():.0f}-{out[c['close']].max():.0f}")
PY
}

dl usa500idxusd  SP500_hist
dl usatechidxusd NASDAQ_hist

echo
echo "Дальше:  .venv/bin/python conductor_check.py --source moex --target iss_Si --leader inv_SP500_hist --h 5 --day-bars 900"
