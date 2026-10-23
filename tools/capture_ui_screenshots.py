#!/usr/bin/env python3
"""
Captures high-resolution responsive screenshots of the FireShield Editorial Suite
at 1440px desktop, 1366px laptop, and 390px mobile viewports using Edge headless.
"""

import os
import pathlib
import shutil
import sys
import threading
import time
from wsgiref.simple_server import make_server

# Ensure workspace root in path
WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from api_adapter import create_wsgi_app
from selenium import webdriver
from selenium.webdriver.edge.options import Options

ARTIFACT_DIR = pathlib.Path(r"C:\Users\ATEC\.gemini\antigravity\brain\fa7d7f58-837d-4016-b40c-a9bd3846eb38")
SCREENSHOTS_DIR = WORKSPACE_ROOT / "screenshots"


def run_server():
    app = create_wsgi_app()
    server = make_server("127.0.0.1", 8000, app)
    server.serve_forever()


def main():
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    print("[+] Starting background WSGI server on 127.0.0.1:8000...")
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    time.sleep(1.5)

    print("[+] Launching Selenium Edge Headless...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--hide-scrollbars")

    driver = webdriver.Edge(options=options)

    viewports = [
        ("fireshield_workbench_1440.png", 1440, 900, "Desktop Workbench (1440px)"),
        ("fireshield_workbench_1366.png", 1366, 768, "Laptop Viewport (1366px)"),
        ("fireshield_mobile_390.png", 390, 844, "Mobile Inspection Viewport (390px)"),
    ]

    try:
        for filename, width, height, label in viewports:
            print(f"[+] Capturing {label} at {width}x{height}...")
            driver.set_window_size(width, height)
            driver.get("http://127.0.0.1:8000/")
            time.sleep(2.0)  # Wait for API fetch and canvas render

            target_path = SCREENSHOTS_DIR / filename
            driver.save_screenshot(str(target_path))
            print(f"    Saved: {target_path} ({target_path.stat().st_size} bytes)")

            # Also copy to artifact directory
            artifact_target = ARTIFACT_DIR / filename
            shutil.copy2(target_path, artifact_target)
            print(f"    Copied to artifact: {artifact_target}")

    finally:
        driver.quit()
        print("[+] Finished screenshot capture successfully!")


if __name__ == "__main__":
    main()
