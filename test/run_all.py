"""Runs every test_*.py in this directory as a subprocess and reports pass/fail.
Plain-script regression tests (no pytest dependency, since this project has no
package manager access to install one) - each file is self-contained and
exits non-zero on failure via its own assertions.

Run: python3 test/run_all.py
"""
import pathlib
import subprocess
import sys

TEST_DIR = pathlib.Path(__file__).parent

if __name__ == "__main__":
    test_files = sorted(TEST_DIR.glob("test_*.py"))
    failures = []

    for test_file in test_files:
        print(f"--- {test_file.name} ---")
        sys.stdout.flush()
        result = subprocess.run([sys.executable, str(test_file)])
        if result.returncode != 0:
            failures.append(test_file.name)
        print()

    if failures:
        print(f"FAILED: {', '.join(failures)}")
        sys.exit(1)

    print(f"All {len(test_files)} test files passed.")
