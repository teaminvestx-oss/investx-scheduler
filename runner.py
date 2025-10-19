# Ejecuta SIEMPRE las noticias y, si procede (lunes ~13h local), el earnings preview.
# Comando cron en Render:  python runner.py

import os, sys, subprocess
from zoneinfo import ZoneInfo
from datetime import datetime

LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))
H1 = int(os.getenv("EARNINGS_MORNING_FROM_H", "12"))  # por defecto 12–14
H2 = int(os.getenv("EARNINGS_MORNING_TO_H",   "14"))

def should_run_earnings(now_local):
    return now_local.weekday() == 0 and (H1 <= now_local.hour <= H2)

def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd)

def main():
    # 1) Noticias (tu script actual está en raíz)
    news_path = "news.py"
    if not os.path.exists(news_path):
        print("No se encuentra news.py", flush=True)
    else:
        run([sys.executable, news_path])

    # 2) Earnings preview si toca
    if should_run_earnings(datetime.now(LOCAL_TZ)):
        run([sys.executable, "earnings_weekly.py"])

if __name__ == "__main__":
    main()
