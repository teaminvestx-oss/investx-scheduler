# runner.py (raíz)
import os, sys, subprocess, datetime

def run(cmd: list[str]) -> int:
    print(f"[runner] {datetime.datetime.utcnow().isoformat()}Z ->", " ".join(cmd), flush=True)
    return subprocess.call(cmd)

def main():
    # Noticias
    news_path = "news.py"
    if os.path.exists(news_path):
        run([sys.executable, news_path])
    else:
        print("[runner] news.py no encontrado", flush=True)

    # Earnings (decide internamente si publica)
    earnings_path = "earnings_weekly.py"
    if os.path.exists(earnings_path):
        run([sys.executable, earnings_path])
    else:
        print("[runner] earnings_weekly.py no encontrado", flush=True)

if __name__ == "__main__":
    main()
