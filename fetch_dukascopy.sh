#!/bin/bash
# Скачать минутки любого инструмента Dukascopy и положить в формат проекта.
# Запуск:  bash fetch_dukascopy.sh <instrument> <ИМЯ> [С_ДАТЫ]
#   bash fetch_dukascopy.sh usdcnh USDCNH_hist 2023-01-01   -> moex_data/inv_USDCNH_hist.csv
set -e
cd "$(dirname "$0")"
INS=$1; NAME=$2; FROM=${3:-2023-01-01}; TO=$(date -u +%F)
PY=$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
mkdir -p moex_data .dukascopy
if [ ! -s ".dukascopy/$NAME.csv" ]; then
  npx --yes dukascopy-node -i "$INS" -from "$FROM" -to "$TO" -t m1 -f csv \
      -dir .dukascopy -fn "$NAME" -v
else
  echo "== $NAME: сырой файл уже есть, качать не надо"
fi
echo "  строк в сырье: $(wc -l < ".dukascopy/$NAME.csv")"
"$PY" - "$NAME" <<'PY'
import sys, pandas as pd
n = sys.argv[1]
d = pd.read_csv(f".dukascopy/{n}.csv")
c = {x.lower(): x for x in d.columns}
ts = c.get("timestamp") or d.columns[0]
d["time"] = (pd.to_datetime(d[ts], unit="ms") if pd.api.types.is_numeric_dtype(d[ts])
             else pd.to_datetime(d[ts]).dt.tz_localize(None))
cols = ["time"] + [c[k] for k in ("open", "high", "low", "close", "volume") if k in c]
out = d[cols].drop_duplicates("time").sort_values("time")
out.to_csv(f"moex_data/inv_{n}.csv", index=False)
print(f"  moex_data/inv_{n}.csv: {len(out)} минут, {out['time'].iloc[0]} – {out['time'].iloc[-1]}, "
      f"close {out[c['close']].min():.4f}-{out[c['close']].max():.4f}")
PY
echo "  (сырой .dukascopy/$NAME.csv оставлен; удалить вручную, когда убедитесь в диапазоне)"
