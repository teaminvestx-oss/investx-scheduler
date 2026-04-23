#!/usr/bin/env python3
# === instagram/test_render.py ===
# Prueba local del render de la card. Genera /tmp/insider_card.png con datos
# de ejemplo y lo abre automáticamente.
#
# Uso:
#   cd /ruta/a/investx-scheduler
#   pip install playwright jinja2
#   playwright install chromium
#   python instagram/test_render.py

import subprocess
import sys
from pathlib import Path

# Asegurar que el directorio raíz del proyecto está en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

from instagram.render_card import render_insider_card

DATOS_PRUEBA = {
    "week_label": "Semana 7–11 abr",
    "buys": 2,
    "sells": 1,
    "companies": 3,
    "trades_by_day": {
        "MAR 8 ABR": [
            {
                "type":    "COMPRA",
                "name":    "Jensen Huang",
                "role":    "CEO",
                "company": "Nvidia",
                "ticker":  "NVDA",
                "amount":  "$10.5M",
                "shares":  "12.000 acc.",
            },
            {
                "type":    "COMPRA",
                "name":    "Sundar Pichai",
                "role":    "CEO",
                "company": "Alphabet",
                "ticker":  "GOOGL",
                "amount":  "$857K",
                "shares":  "5.000 acc.",
            },
        ],
        "JUE 10 ABR": [
            {
                "type":    "VENTA",
                "name":    "Mark Zuckerberg",
                "role":    "CEO",
                "company": "Meta",
                "ticker":  "META",
                "amount":  "$25.6M",
                "shares":  "50.000 acc.",
            },
        ],
    },
    "extra_trades": 3,
    "lectura": (
        "Balance <strong>comprador en semiconductores</strong>. "
        "Dos CEOs comprando en mercado abierto — convicción interna fuerte. "
        "Venta Zuckerberg es plan <strong>10b5-1</strong> programado, "
        "sin valor informativo. Vigilar <strong>NVDA y GOOGL</strong>."
    ),
    "tags": ["NVDA", "GOOGL", "Semiconductores"],
}


if __name__ == "__main__":
    output = "/tmp/insider_card.png"
    print("Renderizando card...")
    path = render_insider_card(DATOS_PRUEBA, output_path=output)
    print(f"\nCard generada en: {path}")

    # Abrir automáticamente en macOS / Linux
    try:
        subprocess.run(["open", path], check=True)   # macOS
    except Exception:
        try:
            subprocess.run(["xdg-open", path], check=True)  # Linux
        except Exception:
            print("Abre manualmente el archivo para ver el resultado.")
