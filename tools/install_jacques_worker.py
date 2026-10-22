"""Opt-in macOS LaunchAgent installation for polling GitHub Issues each minute."""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LABEL = "com.jacques.spotify-queue-worker"
AGENTS = Path.home() / "Library" / "LaunchAgents"
PLIST = AGENTS / (LABEL + ".plist")
LOGS = Path.home() / "Library" / "Logs"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("LaunchAgent support is macOS-only")
    if args.install == args.uninstall:
        raise SystemExit("Choose --install or --uninstall")
    domain = "gui/" + str(os.getuid())
    if args.uninstall:
        if PLIST.exists():
            subprocess.run(["launchctl", "bootout", domain, str(PLIST)], check=False)
            PLIST.unlink()
        print("Jacques worker disabled.")
        return
    if PLIST.exists():
        raise SystemExit("Already installed; --uninstall before reinstalling")
    gh = shutil.which("gh")
    if not gh:
        raise SystemExit("GitHub CLI missing. Install gh and gh auth login first.")
    AGENTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    settings = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(REPO / "tools" / "jacques_mac_worker.py")],
        "WorkingDirectory": str(REPO),
        "RunAtLoad": True,
        "StartInterval": 60,
        "StandardOutPath": str(LOGS / "jacques-worker.log"),
        "StandardErrorPath": str(LOGS / "jacques-worker.err.log"),
        "EnvironmentVariables": {
            "PATH": str(Path(gh).parent) + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(Path.home()),
        },
    }
    with PLIST.open("wb") as f:
        plistlib.dump(settings, f)
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=True)
    print("Jacques worker installed; polls once per minute via launchd.")
    print("To stop: python tools/install_jacques_worker.py --uninstall")

if __name__ == "__main__":
    main()
