# Runner (raíz del repo)
# - Ejecuta noticias siempre (news.py)
# - Ejecuta earnings_weekly.py siempre (él decide si publicar según día/hora)

import os, sys, subprocess

def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd)

def main():
    # 1) Noticias
    news_path = "news.py"
    if os.path.exists(news_path):
        run([sys.executable, news_path])
    else:
        print("Aviso: no se encontró news.py en la raíz", flush=True)

    # 2) Earnings (decide internamente si publicar)
    earnings_path = "earnings_weekly.py"
    if os.path.exists(earnings_path):
        run([sys.executable, earnings_path])
    else:
        print("Aviso: no se encontró earnings_weekly.py en la raíz", flush=True)

if __name__ == "__main__":
    main()
