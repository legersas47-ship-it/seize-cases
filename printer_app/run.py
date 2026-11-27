"""Lance tout : serveur + tunnel public cloudflared + publication de l'adresse sur le site GitHub.

Usage :  python run.py            (tout)
         python run.py --local    (sans tunnel ni publication, pour tester)
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import server

REPO = Path(__file__).resolve().parent.parent
SITE_CFG = REPO / "docs" / "config.json"


def find_exe(name: str) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    for cand in (Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
                 Path(r"C:\Program Files\cloudflared\cloudflared.exe"),
                 Path(r"C:\Program Files\Git\cmd\git.exe")):
        if cand.name.startswith(name) and cand.exists():
            return str(cand)
    return None


def publish(url: str):
    SITE_CFG.write_text(json.dumps({"api": url, "updated": int(time.time())}, indent=2), encoding="utf-8")
    git = find_exe("git") or "git"
    cmds = [[git, "add", "docs/config.json"],
            [git, "commit", "-m", "Nouvelle adresse du serveur d'impression"],
            [git, "push"]]
    for c in cmds:
        r = subprocess.run(c, cwd=REPO, capture_output=True, text=True)
        if r.returncode and c[1] != "commit":
            print("[git]", r.stdout, r.stderr)
    print(f"[site] adresse publiee : {url} (GitHub Pages se met a jour en ~1 min)")


def tunnel():
    exe = find_exe("cloudflared")
    if not exe:
        print("[tunnel] cloudflared introuvable : le site public ne pourra pas joindre l'ordi.")
        return
    port = server.CFG["public_port"]
    while True:
        proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                                stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
        published = False
        for line in proc.stderr:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not published:
                published = True
                print(f"[tunnel] {m.group(0)}")
                threading.Thread(target=publish, args=(m.group(0),), daemon=True).start()
        proc.wait()
        print("[tunnel] coupe, relance dans 5 s...")
        time.sleep(5)


if __name__ == "__main__":
    print(f"Panneau admin : http://127.0.0.1:{server.CFG['admin_port']}")
    if "--local" not in sys.argv:
        threading.Thread(target=tunnel, daemon=True).start()
    server.start()
