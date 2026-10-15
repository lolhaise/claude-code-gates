import json
import os
import subprocess
import sys

import _gate
import stop_chain

BOTH = ("Settings hot-reload on their own, and Gemini currently supports a two million "
        "token context window for every paid plan.")
BOTHV = json.dumps({"mechanism_claim": True, "quote_mechanism": "Settings hot-reload on their own",
                    "stale_world_claim": True, "quote_stale": "Gemini currently supports a two million token context window"})
CASUAL = "The bakery on the corner was out of rye again, so I got the sourdough instead of it."


def run(t):
    def chain(msg, verdict=BOTHV, active=False):
        with t.env(CLAUDE_GATES_JUDGE_FAKE=verdict):
            return stop_chain.run_chain(t.event(msg, active=active))

    _gate.JUDGE_CALLS[0] = 0
    r = chain(BOTH)
    t.check("chain: exactly ONE judge call when both tripwires open", _gate.JUDGE_CALLS[0] == 1, _gate.JUDGE_CALLS[0])
    t.check("chain: runtime-truth takes precedence", bool(r) and r[0] == "runtime", r)
    _gate.JUDGE_CALLS[0] = 0
    t.check("chain: neither tripwire open spends NOTHING", chain(CASUAL) is None and _gate.JUDGE_CALLS[0] == 0)
    _gate.JUDGE_CALLS[0] = 0
    r = chain(BOTH.replace(", and", " \u2014 and"))
    t.check("chain: a free gate wins before any judge call",
            bool(r) and r[0] == "slop" and _gate.JUDGE_CALLS[0] == 0, (r, _gate.JUDGE_CALLS[0]))
    t.check("chain: stop_hook_active stands every gate down", chain(BOTH, active=True) is None)
    prompt = _gate.judge_prompt([__import__("runtime_truth").QUESTION, __import__("cutoff_tripwire").QUESTION], BOTH)
    t.check("chain: the shared prompt asks both questions",
            "mechanism_claim" in prompt and "stale_world_claim" in prompt and BOTH in prompt)

    hook = os.path.join(t.hooks, "stop_chain.py")
    ev = json.dumps(t.event("This is fine \u2014 until it isn't, and then it is not."))
    p = subprocess.run([sys.executable, hook], input=ev, capture_output=True, text=True, timeout=30)
    out = json.loads(p.stdout or "{}")
    t.check("chain (process): bounce is a block decision with exit 0",
            p.returncode == 0 and out.get("decision") == "block" and "[slop gate]" in out.get("reason", ""), p.stdout)
    env = dict(os.environ, CLAUDE_GATES_JUDGE_CHILD="1")
    p = subprocess.run([sys.executable, hook], input=ev, capture_output=True, text=True, timeout=30, env=env)
    t.check("chain (process): inside the judge's own session it exits 0 silently",
            p.returncode == 0 and p.stdout == "", p.stdout)
    p = subprocess.run([sys.executable, hook], input="{{{ not json", capture_output=True, text=True, timeout=30)
    t.check("chain (process): bad stdin exits 0 silently", p.returncode == 0 and p.stdout == "", p.stdout)
