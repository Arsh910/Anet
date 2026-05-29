try:
    from PyQt5.QtCore import QSettings
    print("PyQt5 imported")
    # This is often where it hangs/crashes in headless envs if not handled
    settings = QSettings("test.ini", QSettings.IniFormat)
    print("QSettings initialized")
except Exception as e:
    print(f"Error: {e}")
