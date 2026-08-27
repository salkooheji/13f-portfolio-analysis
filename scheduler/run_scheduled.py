"""Wrapper for scheduled execution via Windows Task Scheduler.

Task Scheduler starts processes with no venv and an arbitrary
working directory, so this wrapper pins both: it resolves the repo
root from its own file location and re-executes the pipeline using
the venv's Python directly. Keeping it tiny means the scheduled
path and the manual path run identical code."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


def main() -> int:
    result = subprocess.run(
        [str(VENV_PYTHON), "run_pipeline.py", "--triggered-by", "scheduled"],
        cwd=str(REPO_ROOT),
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
    