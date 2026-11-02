"""Conservative desktop Chrome launcher.

Use standard Selenium and an explicitly installed system ChromeDriver.
Do not kill processes, disable sandbox, or download executable drivers.
Live Google sign-in has not been tested.
"""
import re
import shutil
import subprocess


def _major(binary: str) -> str:
    try:
        output = subprocess.check_output([binary, "--version"], text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Cannot check browser/driver version: {binary}") from exc
    match = re.search(r"\b(\d+)\.\d+\.", output)
    if not match:
        raise RuntimeError(f"Unrecognized version from {binary}")
    return match.group(1)


def _select_browser_binary():
    # Arch/CachyOS AUR google-chrome installs google-chrome-stable,
    # not necessarily google-chrome.
    for name in ("chromium", "google-chrome-stable", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def create_driver():
    browser = _select_browser_binary()
    driver = shutil.which("chromedriver")
    if not browser or not driver:
        raise RuntimeError(
            "Matching Chromium/Chrome and chromedriver are required. "
            "On CachyOS, the Arch 'chromium' package provides both. "
            "Nothing has been downloaded or installed automatically."
        )
    if _major(browser) != _major(driver):
        raise RuntimeError(
            "Chrome/Chromium and chromedriver major versions differ; update them together."
        )

    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    options = webdriver.ChromeOptions()
    options.binary_location = browser
    options.add_argument("--start-maximized")
    return webdriver.Chrome(service=Service(executable_path=driver), options=options)
