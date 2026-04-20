# === instagram/render_card.py ===
# Renders the insider trading Instagram card to a PNG using Playwright.
# Requires: pip install playwright jinja2 && playwright install chromium

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent / "templates"
CARD_WIDTH   = 1080
CARD_HEIGHT  = 1350
OUTPUT_PATH  = "/tmp/insider_card.png"


def render_insider_card(data: dict, output_path: str = OUTPUT_PATH) -> str:
    """
    Renders the insider trading card template to a PNG file.

    Args:
        data: Dict with keys: week_label, buys, sells, companies,
              trades_by_day, extra_trades, lectura, tags.
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

    print(f"[instagram/render] HTML written to {tmp_html}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(
            viewport={"width": CARD_WIDTH, "height": CARD_HEIGHT},
        )
        page.goto(f"file://{tmp_html}", wait_until="networkidle")
        page.wait_for_timeout(300)
        page.screenshot(
            path=output_path,
            clip={"x": 0, "y": 0, "width": CARD_WIDTH, "height": CARD_HEIGHT},
        )
        browser.close()

    print(f"[instagram/render] Card saved: {output_path}")
    return os.path.abspath(output_path)
