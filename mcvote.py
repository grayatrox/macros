#!/usr/bin/env python3
"""
Minecraft server auto-vote helpers.

Primary entry point:
  open_vote_pages()  open the vote URLs as tabs in the user's REAL Firefox, so
                     navigator.webdriver is false and Cloudflare Turnstile
                     behaves normally. The companion Tampermonkey userscript
                     (strayamc_vote_autofill.user.js) pre-fills the username;
                     you solve the captcha and click vote.

Legacy:
  vote()             full-auto Selenium flow for findmcserver.com pages. Cannot
                     pass Turnstile (geckodriver forces navigator.webdriver=True)
                     — kept only for captcha-free findmcserver-style pages.
                     Needs the optional `legacy-vote` extra (Selenium), which
                     is imported inside the legacy functions so that
                     open_vote_pages() works without it.
"""

from __future__ import annotations

import subprocess
import time
import webbrowser
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selenium.webdriver.firefox.webdriver import WebDriver

Log = Callable[[str], None]

# Configuration
SERVER_URL = "https://findmcserver.com/server/example?vote=true"  # legacy default
USERNAME = "YourMinecraftName"  # Replace with your actual username
WAIT_TIMEOUT = 10  # seconds to wait for elements to load


# ─────────────────────────────────────────────────────────────────────────────
# Firefox detection
# ─────────────────────────────────────────────────────────────────────────────

FIREFOX_PATHS = [
    "C:\\Program Files\\Mozilla Firefox\\firefox.exe",
    "C:\\Program Files (x86)\\Mozilla Firefox\\firefox.exe",
    str(Path.home() / "AppData" / "Local" / "Mozilla Firefox" / "firefox.exe"),
]


def _ignore(_message: str) -> None:
    pass


def _find_firefox(log: Log = _ignore) -> str | None:
    """Path to the installed Firefox, or None."""
    for path in FIREFOX_PATHS:
        if Path(path).exists():
            log(f"✓ Found Firefox at {path}")
            return path
    log("⚠ Could not find Firefox installation")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Open vote pages in the user's real browser (Turnstile-safe)
# ─────────────────────────────────────────────────────────────────────────────


def open_vote_pages(server_urls: str | Iterable[str] | None, callback: Log | None = None) -> int:
    """Open the vote URLs as tabs in the user's REAL Firefox.

    Uses the user's normal browser (not Selenium), so navigator.webdriver is
    false and Cloudflare Turnstile behaves normally. The Tampermonkey userscript
    strayamc_vote_autofill.user.js fills the username on each site. Returns the
    number of URLs opened.
    """
    urls = [server_urls] if isinstance(server_urls, str) else list(server_urls or [])
    log: Log = callback or print

    if not urls:
        log("No vote URLs supplied")
        return 0

    firefox = _find_firefox(log)
    if firefox:
        try:
            subprocess.Popen([firefox, *urls])
            log(f"Opened {len(urls)} vote page(s) in Firefox")
            return len(urls)
        except Exception as e:
            log(f"⚠ Could not launch Firefox directly ({e}); using default browser")
    for url in urls:
        webbrowser.open(url)
    log(f"Opened {len(urls)} vote page(s) in the default browser")
    return len(urls)


# ─────────────────────────────────────────────────────────────────────────────
# Legacy full-auto voting (findmcserver.com, Selenium)
# ─────────────────────────────────────────────────────────────────────────────


def _firefox_user_agent(firefox_binary: str | None, log: Log) -> str | None:
    """Build the UA string real Firefox sends. Firefox's UA is deterministic from
    its major version and the OS, and freezes the point release, e.g.
      Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0
    """
    version = None
    if firefox_binary:
        ini = Path(firefox_binary).parent / "application.ini"
        try:
            with ini.open(encoding="utf-8") as f:
                for line in f:
                    if line.startswith("Version="):
                        version = line.split("=", 1)[1].strip()
                        break
        except OSError:
            pass
    if not version:
        log("⚠ Could not read Firefox version — using its default user agent")
        return None
    major = version.split(".")[0]
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{major}.0) Gecko/20100101 Firefox/{major}.0"
    log(f"Spoofing user agent as Firefox {major} (installed: {version})")
    return ua


def _make_driver(headless: bool, log: Log) -> WebDriver | None:
    """Locate geckodriver + Firefox and return a started WebDriver, or None.

    The real browser's user agent is used, but this CANNOT hide
    navigator.webdriver (geckodriver forces it True), so Cloudflare Turnstile
    sites will still reject it — use open_vote_pages() for those.
    """
    from selenium import webdriver  # noqa: PLC0415 - optional legacy-vote extra
    from selenium.webdriver.firefox.service import Service  # noqa: PLC0415 - ditto

    script_dir = Path(__file__).resolve().parent
    geckodriver_path = script_dir / "geckodriver.exe"

    if not geckodriver_path.exists():
        log(f"Error: geckodriver.exe not found in {script_dir}")
        log("Download it from: https://github.com/mozilla/geckodriver/releases")
        return None
    log(f"✓ Found geckodriver.exe at {geckodriver_path}")

    firefox_binary = _find_firefox(log)

    options = webdriver.FirefoxOptions()
    if firefox_binary:
        options.binary_location = firefox_binary
    options.add_argument("--width=1024")
    options.add_argument("--height=768")
    if headless:
        options.add_argument("--headless")
        log("Running in headless mode")

    user_agent = _firefox_user_agent(firefox_binary, log)
    if user_agent:
        options.set_preference("general.useragent.override", user_agent)

    try:
        service = Service(str(geckodriver_path))
        log("Starting Firefox...")
        return webdriver.Firefox(service=service, options=options)
    except Exception as e:
        log(f"Error starting Firefox: {e}")
        return None


def _cast_vote(driver: WebDriver, url: str, log: Log) -> bool:
    """Vote on a single findmcserver page in the open browser."""
    from selenium.webdriver.common.by import By  # noqa: PLC0415 - optional legacy-vote extra
    from selenium.webdriver.support import expected_conditions  # noqa: PLC0415 - ditto
    from selenium.webdriver.support.ui import WebDriverWait  # noqa: PLC0415 - ditto

    log(f"Opening {url}...")
    driver.get(url)

    log("Waiting for page to load...")
    WebDriverWait(driver, WAIT_TIMEOUT).until(
        expected_conditions.presence_of_all_elements_located((By.TAG_NAME, "body"))
    )

    log("Waiting for voting form to render...")
    time.sleep(5)

    try:
        username_field = WebDriverWait(driver, 15).until(
            expected_conditions.visibility_of_element_located(
                (By.CSS_SELECTOR, "input[placeholder='Minecraft Username']")
            )
        )
        log("Found username field!")
        log(f"Filling username: {USERNAME}")
        username_field.clear()
        username_field.send_keys(USERNAME)
        log("✓ Username filled in!")
    except Exception as e:
        log(f"⚠ Error finding username field: {e}")
        return False

    try:
        vote_button = driver.find_element(
            By.CSS_SELECTOR, "button[aria-label='Vote for the Server']"
        )
        log("✓ Vote button found!")
        log("Clicking vote button...")
        vote_button.click()
        log("✓ Vote submitted!")
        time.sleep(2)
    except Exception as e:
        log(f"⚠ Error clicking vote button: {e}")
        return False
    return True


def vote(
    callback: Log | None = None,
    headless: bool = True,
    server_urls: str | Iterable[str] | None = None,
) -> int:
    """Full-auto voting for findmcserver.com pages in one browser session.

    server_urls defaults to [SERVER_URL] (example); the scraping assumes a
    findmcserver.com layout. Returns the number of pages successfully voted on.
    """
    if server_urls is None:
        urls = [SERVER_URL]
    elif isinstance(server_urls, str):
        urls = [server_urls]
    else:
        urls = list(server_urls)

    log: Log = callback or print

    if not urls:
        log("No vote URLs supplied")
        return 0

    driver = _make_driver(headless, log)
    if driver is None:
        return 0

    successes = 0
    try:
        for i, url in enumerate(urls, 1):
            log(f"── Vote {i}/{len(urls)} ──")
            try:
                if _cast_vote(driver, url, log):
                    successes += 1
            except Exception as e:
                # One bad page must not abort the rest of the list.
                log(f"Error voting on {url}: {e}")
        log(f"Done: {successes}/{len(urls)} vote(s) submitted")
    finally:
        log("Closing browser...")
        time.sleep(1)
        driver.quit()

    return successes


if __name__ == "__main__":
    vote()
