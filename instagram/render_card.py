# === instagram/render_card.py ===
# Renders the insider trading card to a 1080x1350 PNG.
#
# Strategy: the template is designed at 340 px (natural card width).
# Playwright renders at device_scale_factor = 1080/340 ≈ 3.176, which
# produces a physical 1080 px wide output without any CSS scaling tricks.
#
# Requires: pip install playwright jinja2 && playwright install chromium

import math
import os
import subprocess
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR   = Path(__file__).parent / "templates"
CARD_WIDTH     = 1080
CARD_HEIGHT    = 1350
NATURAL_WIDTH  = 340
DPR            = CARD_WIDTH / NATURAL_WIDTH          # 3.1764...
NATURAL_HEIGHT = math.ceil(CARD_HEIGHT / DPR)       # 425 px — CSS clip height
OUTPUT_PATH    = "/tmp/insider_card.png"


def render_insider_card(data: dict, output_path: str = OUTPUT_PATH) -> str:
    """
    Renders the insider trading card template to a 1080x1350 PNG.

    Args:
        data: Dict with keys: week_label, buys, sells, companies,
              trades_by_day (dict[day_label → list[trade_dict]]),
              extra_trades, lectura, tags.
        output_path: Destination path for the PNG.

    Returns:
        Absolute path to the rendered PNG.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("insider.html")
    html_content = template.render(**data)

    tmp_html = "/tmp/insider_card.html"
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"[instagram/render] HTML escrito en {tmp_html}")

    # Instala Chromium si no está disponible (necesario en Render donde el
    # cache de /opt/render/.cache/ms-playwright no persiste entre runs)
    try:
        from playwright.sync_api import sync_playwright as _check
        with _check() as _p:
            _p.chromium.executable_path  # lanza excepción si no existe
    except Exception:
        print("[instagram/render] Chromium no encontrado, instalando...")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        print("[instagram/render] Chromium instalado.")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            viewport={"width": NATURAL_WIDTH, "height": NATURAL_HEIGHT + 60},
            device_scale_factor=DPR,
        )
        page = context.new_page()
        page.goto(f"file://{tmp_html}", wait_until="networkidle")
        page.wait_for_timeout(300)

        # clip en CSS px → output PNG = NATURAL_WIDTH*DPR x NATURAL_HEIGHT*DPR = 1080x1350
        page.screenshot(
            path=output_path,
            clip={"x": 0, "y": 0, "width": NATURAL_WIDTH, "height": NATURAL_HEIGHT},
        )
        browser.close()

    print(f"[instagram/render] Card guardada: {output_path}")
    return os.path.abspath(output_path)
