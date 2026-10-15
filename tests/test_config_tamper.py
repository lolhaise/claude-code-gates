import json
import os
import subprocess
import sys
import time

import config_tamper as ct

BASE = {"hooks": {
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/g/deny_list.py", "timeout": 10}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "/g/stop_chain.py", "timeout": 45}]}],
}}


def run(t):
    sp = t.path("settings.json")

    def write(obj):
        with open(sp, "w") as f:
            json.dump(obj, f)

    def fire(source="user_settings"):
        return ct.handle({"hook_event_name": "ConfigChange", "source": source, "file_path": sp, "session_id": "s"})

    def last_row():
        with open(ct.ledger_path()) as f:
            return json.loads(f.read().splitlines()[-1])

    write(BASE)
    t.check("config: first sight bootstraps and allows", fire() == "" and last_row()["verdict"] == "bootstrap")
    t.check("config: unchanged hooks allow", fire() == "" and last_row()["verdict"] == "other")
    removed = json.loads(json.dumps(BASE))
    del removed["hooks"]["Stop"]
    write(removed)
    out = fire()
    t.check("config: a removed hook BLOCKS", json.loads(out or "{}").get("decision") == "block", out)
    t.check("config: the block is ledgered", last_row()["verdict"] == "blocked" and len(last_row()["removed"]) == 1)
    t.check("config: a blocked change does not move the baseline", fire() != "")
    swapped = json.loads(json.dumps(BASE))
    swapped["hooks"]["Stop"][0]["hooks"][0]["command"] = "/tmp/true"
    write(swapped)
    t.check("config: a swapped command BLOCKS as altered",
            bool(fire()) and len(last_row()["altered"]) == 1 and not last_row()["removed"])
    write(removed)
    open(ct.release_path(), "w").close()
    t.check("config: a fresh ALLOW release lets one removal through",
            fire() == "" and last_row()["verdict"] == "removed-with-release")
    t.check("config: the release is single use", not os.path.exists(ct.release_path()))
    t.check("config: after a release the new set is the baseline", fire() == "")
    write(BASE)
    t.check("config: an added hook allows and becomes the baseline", fire() == "" and last_row()["verdict"] == "added")
    write(removed)
    open(ct.release_path(), "w").close()
    old = time.time() - 3600
    os.utime(ct.release_path(), (old, old))
    t.check("config: a stale release (older than 15 min) does not allow", bool(fire()))
    os.remove(ct.release_path())
    write(BASE)
    fire()
    longer = json.loads(json.dumps(BASE))
    longer["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 60
    write(longer)
    t.check("config: a timeout change allows", fire() == "" and last_row()["verdict"] == "timeout-changed")
    write(removed)
    with t.env(CLAUDE_GATE_CONFIG="off"):
        t.check("config: OFF is log-only", fire() == "" and last_row().get("would_block") is True)
    t.check("config: OFF did not bless the removal (baseline frozen)", bool(fire()))
    write(longer)
    t.check("config: project settings are logged, never blocked",
            fire("project_settings") == "" and last_row()["verdict"] == "other-source")
    with open(sp, "w") as f:
        f.write("{ broken json")
    t.check("config: unparseable settings FAIL OPEN", fire() == "" and last_row()["verdict"] == "unparseable")
    write(BASE)
    with open(ct.baseline_path(sp), "w") as f:
        f.write("garbage")
    t.check("config: a corrupt baseline re-bootstraps instead of blocking",
            fire() == "" and last_row()["verdict"] == "bootstrap")
    hook = os.path.join(t.hooks, "config_tamper.py")
    p = subprocess.run([sys.executable, hook, "--baseline", sp], capture_output=True, text=True, timeout=30)
    t.check("config (process): --baseline captures the current hook set",
            p.returncode == 0 and "2 hook handler(s)" in p.stdout, p.stdout + p.stderr)
    p = subprocess.run([sys.executable, hook], input="nope", capture_output=True, text=True, timeout=30)
    t.check("config (process): bad stdin allows silently", p.returncode == 0 and p.stdout == "")
