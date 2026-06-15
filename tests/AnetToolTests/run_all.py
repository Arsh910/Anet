"""
Run every tool's test.py in its own subprocess and print a summary.

    python tests/AnetToolTests/run_all.py

Each <tool>/test.py is also runnable on its own:
    python tests/AnetToolTests/<tool>/test.py

(If pytest is installed, `pytest tests/AnetToolTests` works too — the test_*
functions are plain sync functions.)
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    tests = sorted(HERE.glob("*/test.py"))
    passed, failed = [], []
    for t in tests:
        tool = t.parent.name
        proc = subprocess.run(
            [sys.executable, str(t)],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            passed.append(tool)
            print(f"  PASS  {tool}")
        else:
            failed.append(tool)
            print(f"  FAIL  {tool}")
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
            for line in tail:
                print(f"        {line}")

    print(f"\n{len(passed)}/{len(tests)} tool test files passed.")
    if failed:
        print("Failed:", ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
