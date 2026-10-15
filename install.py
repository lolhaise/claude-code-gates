#!/usr/bin/env python3
"""Merge this repo's hooks into an existing ~/.claude/settings.json.

    python3 ~/.claude/gates/install.py [path/to/settings.json]

Idempotent: a handler whose command is already present is skipped, so running it twice
changes nothing. The original file is copied to settings.json.bak-gates first, every other
key (permissions, env, model, your own hooks) is left as it was, and the config tamper
gate's baseline is captured from the merged result.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def merge(settings, example):
    added = 0
    hooks = settings.setdefault("hooks", {})
    for event, groups in example["hooks"].items():
        have = {h.get("command") for g in hooks.get(event, []) for h in g.get("hooks", [])}
        for g in groups:
            new = [h for h in g["hooks"] if h["command"] not in have]
            if new:
                hooks.setdefault(event, []).append(dict(g, hooks=new))
                added += len(new)
    return added


def main(argv):
    path = os.path.expanduser(argv[0] if argv else "~/.claude/settings.json")
    with open(os.path.join(HERE, "settings.example.json")) as f:
        example = json.load(f)
    settings = {}
    if os.path.exists(path):
        with open(path) as f:
            settings = json.load(f)
        shutil.copy2(path, path + ".bak-gates")
    added = merge(settings, example)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(path + ".tmp", path)
    subprocess.run([sys.executable, os.path.join(HERE, "hooks", "config_tamper.py"), "--baseline", path])
    print("%d hook handler(s) added to %s" % (added, path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
