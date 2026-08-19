"""Render the architecture diagram from its single source of truth.

scripts/architecture.html IS the diagram — every module name in it matches the steps[]
logging verbatim (the course grades that consistency). This script renders it to the two
served copies. Requires playwright (`pip install playwright && playwright install chromium`).

Run: python scripts/render_architecture.py
"""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "scripts", "architecture.html")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.goto("file:///" + SRC.replace("\\", "/"), wait_until="networkidle")
        page.wait_for_timeout(1200)  # let the Google Fonts settle
        for out in ("static", "public"):
            page.screenshot(path=os.path.join(ROOT, out, "architecture.png"))
            print("wrote", os.path.join(ROOT, out, "architecture.png"))
        browser.close()


if __name__ == "__main__":
    main()
