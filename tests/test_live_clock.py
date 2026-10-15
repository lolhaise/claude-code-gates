import calendar
import json
import os
import re
import subprocess
import sys
import time

import live_clock


def run(t):
    line = live_clock.clock_line({})
    t.check("clock: line has weekday, date, time and zone",
            bool(re.match(r"^\[live-clock\] [A-Z][a-z]+day \d{4}-\d{2}-\d{2} \d{2}:\d{2} \S+$", line)), line)
    tp = t.transcript()                       # first row is stamped 2026-01-01T10:00:00Z
    t0 = calendar.timegm(time.strptime("2026-01-01T10:00:00", "%Y-%m-%dT%H:%M:%S"))
    age = live_clock.session_age(tp, now=t0 + 95 * 60)
    t.check("clock: session age computed from the first transcript stamp", "(1h35m in)" in age, age)
    t.check("clock: under an hour reads in minutes", "(22m in)" in live_clock.session_age(tp, now=t0 + 22 * 60))
    t.check("clock: no transcript, no age", live_clock.session_age("/nonexistent") == "")
    hook = os.path.join(t.hooks, "live_clock.py")
    p = subprocess.run([sys.executable, hook], input="{}", capture_output=True, text=True, timeout=30)
    o = json.loads(p.stdout or "{}").get("hookSpecificOutput", {})
    t.check("clock (process): emits UserPromptSubmit additionalContext",
            o.get("hookEventName") == "UserPromptSubmit" and o.get("additionalContext", "").startswith("[live-clock] "), p.stdout)
    env = dict(os.environ, CLAUDE_GATE_CLOCK="off")
    p = subprocess.run([sys.executable, hook], input="{}", capture_output=True, text=True, timeout=30, env=env)
    t.check("clock (process): kill switch injects nothing", p.returncode == 0 and p.stdout == "")
