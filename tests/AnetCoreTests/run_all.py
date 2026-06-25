"""
Run every core test.py (AdaptOrch phases + other core modules) in its own
subprocess and print a summary.

    python tests/AnetCoreTests/run_all.py

Each phase's test is also runnable on its own:
    python tests/AnetCoreTests/<module>/test.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    tests = sorted(HERE.glob("*/test.py"))
    passed, failed = [], []
    for t in tests:
        name = t.parent.name
        proc = subprocess.run([sys.executable, str(t)], capture_output=True, text=True)
        if proc.returncode == 0:
            passed.append(name); print(f"  PASS  {name}")
        else:
            failed.append(name); print(f"  FAIL  {name}")
            for line in (proc.stdout + proc.stderr).strip().splitlines()[-6:]:
                print(f"        {line}")

    print(f"\n{len(passed)}/{len(tests)} core test files passed.")
    if failed:
        print("Failed:", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
