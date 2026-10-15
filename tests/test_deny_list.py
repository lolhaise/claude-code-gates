import json
import os
import subprocess
import sys

import deny_list


def run(t):
    def d(cmd, tool="Bash"):
        hit = deny_list.decide({"tool_name": tool, "tool_input": {"command": cmd}})
        return hit[0] if hit else None

    t.check("deny: bare rm is denied", d("rm notes.txt") == "rm")
    t.check("deny: rm -rf is denied", d("rm -rf build/") == "rm")
    t.check("deny: rm after a chain is denied", d("cd /tmp && rm old.log") == "rm")
    t.check("deny: rm in a subshell is denied", d("echo $(rm x)") == "rm")
    t.check("deny: git rm is exempt", d("git rm --cached secrets.env") is None)
    t.check("deny: npm rm is exempt", d("npm rm left-pad") is None)
    t.check("deny: docker rm is exempt", d("docker rm -f web") is None)
    t.check("deny: apt-get rm style is exempt", d("apt-get rm foo") is None)
    t.check("deny: trash is allowed", d("trash notes.txt") is None)
    t.check("deny: the word rm inside an argument is allowed", d("grep -n 'rm ' script.sh") is None)
    t.check("deny: journalctl --since 'today HH:MM' is denied",
            d('journalctl --user --since "today 09:00" -u app') == "journalctl-today")
    t.check("deny: journalctl --since '6 hours ago' is allowed", d('journalctl --since "6 hours ago"') is None)
    t.check("deny: silenced stderr on a /mnt/c interop call is denied",
            d("/mnt/c/Windows/System32/where.exe python 2>/dev/null") == "interop-stderr")
    t.check("deny: the same interop call with stderr is allowed", d("/mnt/c/Windows/System32/where.exe python") is None)
    t.check("deny: a non-Bash tool is never judged", d("rm x", tool="Write") is None)
    with t.env(CLAUDE_GATE_DENY="off"):
        t.check("deny: env kill switch", d("rm notes.txt") is None)
    off = t.off_file("deny")
    open(off, "w").close()
    t.check("deny: OFF file kill switch", d("rm notes.txt") is None)
    os.remove(off)
    hook = os.path.join(t.hooks, "deny_list.py")
    p = subprocess.run([sys.executable, hook], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm a"}}),
                       capture_output=True, text=True, timeout=30)
    o = json.loads(p.stdout or "{}").get("hookSpecificOutput", {})
    t.check("deny (process): emits a PreToolUse deny with the teaching reason",
            p.returncode == 0 and o.get("permissionDecision") == "deny" and "trash" in o.get("permissionDecisionReason", ""),
            p.stdout)
    p = subprocess.run([sys.executable, hook], input="garbage", capture_output=True, text=True, timeout=30)
    t.check("deny (process): bad stdin allows silently", p.returncode == 0 and p.stdout == "")
