"""Conservative Chrome launcher for the desktop Google Find Hub sign-in.

Unlike the pinned upstream chrome_driver.py, this launcher does not kill other
Chrome sessions or explicitly disable the browser sandbox.
"""
def create_driver():
    import undetected_chromedriver as uc

    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    return uc.Chrome(options=options, version_main=None)
