import json
import os
import subprocess
import sys


def run(t):
    sys.path.insert(0, os.path.dirname(t.hooks))
    import install
    with open(os.path.join(os.path.dirname(t.hooks), "settings.example.json")) as f:
        example = json.load(f)
    s = {}
    t.check("install: merge into an empty file adds every handler", install.merge(s, example) == 6)
    t.check("install: a second merge adds nothing (idempotent)", install.merge(s, example) == 0)
    mine = {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/my/own.sh"}]}]}}
    install.merge(mine, example)
    stop_cmds = [h["command"] for g in mine["hooks"]["Stop"] for h in g["hooks"]]
    t.check("install: the user's own hooks and keys survive",
            mine["model"] == "opus" and "/my/own.sh" in stop_cmds and len(stop_cmds) == 2, stop_cmds)
    sp = t.path("settings.json")
    with open(sp, "w") as f:
        json.dump({"permissions": {"allow": ["Bash(ls:*)"]}}, f)
    p = subprocess.run([sys.executable, os.path.join(os.path.dirname(t.hooks), "install.py"), sp],
                       capture_output=True, text=True, timeout=30)
    with open(sp) as f:
        merged = json.load(f)
    t.check("install (process): merges, keeps a backup and captures the tamper baseline",
            p.returncode == 0 and os.path.exists(sp + ".bak-gates") and "permissions" in merged
            and "Stop" in merged["hooks"] and "baseline captured: 6" in p.stdout, p.stdout + p.stderr)
    for event, groups in example["hooks"].items():
        for g in groups:
            for h in g["hooks"]:
                f = os.path.join(t.hooks, os.path.basename(h["command"]))
                t.check("install: %s points at an executable hook file" % os.path.basename(f),
                        os.path.isfile(f) and os.access(f, os.X_OK), f)
