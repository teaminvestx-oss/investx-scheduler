# runner.py — ejecuta noticias y earnings con logs claros
import os, sys, subprocess, datetime

def runpy(path: str) -> int:
    if os.path.exists(path):
        print(f"[runner] {datetime.datetime.now(datetime.timezone.utc).isoformat()} -> python -u {path}", flush=True)
        return subprocess.call([sys.executable, "-u", path])
    else:
        print(f"[runner] MISSING {path}", flush=True)
        return 0

def main():
    # 1) Noticias (acepta news.py o scripts/news_es.py)
    ret = runpy("news.py")
    if ret != 0:
        print(f"[runner] news.py returned {ret}", flush=True)
    else:
        runpy("scripts/news_es.py")

    # 2) Earnings semanal
    runpy("earnings_weekly.py")

if __name__ == "__main__":
    main()
