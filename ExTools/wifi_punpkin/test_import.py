import sys
import os
sys.path.append("/home/arshdeeppalial/Projects/Anet/ExTools/wifi_punpkin/wifipumpkin3")
print("Attempting import...")
try:
    from wifipumpkin3 import PumpkinShell
    print("Import successful")
except Exception as e:
    print(f"Import failed: {e}")
except BaseException as e:
    print(f"BaseException during import: {e}")
