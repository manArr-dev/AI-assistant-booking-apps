"""
Run every test suite, each in its own process (each gets a fresh, isolated database).

    python run_tests.py                       # SQLite + in-memory cache
    TEST_DATABASE_URL=postgresql://... TEST_REDIS_URL=redis://... python run_tests.py
"""
import subprocess
import sys
import time
from pathlib import Path

SUITES = ["test_auth.py", "test_platform.py", "test_duffel.py", "test_server.py",
          "test_travel.py", "test_api.py"]


def main() -> int:
    here = Path(__file__).parent
    failed = []
    for suite in SUITES:
        start = time.monotonic()
        proc = subprocess.run([sys.executable, suite], cwd=here, capture_output=True, text=True)  # noqa: S603
        took = time.monotonic() - start
        last = (proc.stdout.strip().splitlines() or [""])[-1]
        status = "PASS" if proc.returncode == 0 else "FAIL"
        print(f"{status}  {suite:<18} {took:5.1f}s  {last if proc.returncode == 0 else ''}")
        if proc.returncode:
            failed.append(suite)
            print(proc.stdout[-3000:])
            print(proc.stderr[-3000:], file=sys.stderr)
    print(f"\n{len(SUITES) - len(failed)}/{len(SUITES)} suites passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
