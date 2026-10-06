import json
from pathlib import Path
import subprocess
import sys


def test_runner_free_qc_help():
    proc = subprocess.run(
        [sys.executable, "tools/btc_local_qc.py", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    assert "--reference-json" in proc.stdout
