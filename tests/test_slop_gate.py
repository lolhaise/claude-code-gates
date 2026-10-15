import os

import slop_gate

CASES = [
    ("em dash hard-fails", "This is fine \u2014 until it isn't.", "chat", "FAIL"),
    ("typed -- hard-fails", "It works -- mostly, and that is the problem.", "chat", "FAIL"),
    ("casual slang passes", "lol that's wild. honestly no idea, we'll see. not gonna overthink it.", "chat", "PASS"),
    ("contractions + fragments pass", "Nah. Don't. It's a coin flip tilted your way, and that's fine.", "chat", "PASS"),
    ("not-x-but-y stack fails", "This isn't just a tool, it's a mirror. That's not a bug, it's the whole point.", "chat", "FAIL"),
    ("stacked negation fails", "No setup. No config. No surprises. Ship it.", "chat", "FAIL"),
    ("multi-word stacked negation fails", "No more manual deploys. No more waiting for approval. No more pager. Ship it.", "chat", "FAIL"),
    ("noun-subject pivot counts", "The rebrand isn't a step, it's a launchpad. The beta isn't a release, it's a runway.", "chat", "FAIL"),
    ("here's the thing + let's dive in fails", "Here's the thing: most teams get this backwards. Let's dive in.", "chat", "FAIL"),
    ("single tricolon passes", "It was fast, cheap, and boring. Good.", "chat", "PASS"),
    ("tier words alone do NOT fail", "We should leverage this robust, seamless, cutting-edge tapestry to delve deeper.", "chat", "PASS"),
    ("chatbot residue fails", "Great question! Absolutely! I hope this helps.", "chat", "FAIL"),
    ("NO_REPLY is skipped", "NO_REPLY", "chat", "PASS"),
    ("code blocks are not judged", "run this:\n```\necho 'not X \u2014 but Y'\n```\nthen done.", "chat", "PASS"),
    ("urls are not judged", "see <https://example.com/not-x-but-y\u2014really> for the file.", "chat", "PASS"),
    ("external profile is stricter", "This isn't just an update, it's a step change.", "external", "FAIL"),
    ("uniform cadence fails", " ".join(["The system works well today."] * 9), "chat", "FAIL"),
    ("varied real prose passes", "Nope. The deploy at noon changed nothing in the API. What broke was a single "
     "renamed column in the nightly export, and the dashboard read it as empty. I fixed the mapping. Rerun the job "
     "and the numbers come back.", "chat", "PASS"),
]

LIST = "\n".join("- leg %d: %d ms, slow end %d ms" % (i, 100 + i, 200 + i) for i in range(10))


def run(t):
    for name, txt, prof, want in CASES:
        got = slop_gate.analyse(txt, prof)["verdict"]
        t.check("slop: " + name, got == want, "got %s" % got)

    def gate(msg, active=False):
        return slop_gate.check(t.event(msg, active=active))

    r = gate("This is fine \u2014 until it isn't, and then it is not.")
    t.check("slop-hook: em dash BLOCKS", bool(r) and "[slop gate]" in r and "em-dash" in r, r)
    t.check("slop-hook: reason quotes the spot", bool(r) and "until" in r, r)
    t.check("slop-hook: clean reply ALLOWS", gate("Done. The build passes and the file is where you asked.") is None)
    t.check("slop-hook: stop_hook_active never loops", gate("This is fine \u2014 until it isn't.", active=True) is None)
    t.check("slop-hook: NO_REPLY never judged", gate("NO_REPLY") is None)
    t.check("slop: a list is exempt from the uniformity rule", slop_gate.analyse(LIST)["verdict"] == "PASS",
            slop_gate.analyse(LIST)["findings"])
    with t.env(CLAUDE_GATE_SLOP_DASH="allow"):
        v = slop_gate.analyse("This reply is a normal length and reads fine \u2014 apart from the dash.")
        t.check("slop: CLAUDE_GATE_SLOP_DASH=allow makes the dash report-only",
                v["verdict"] == "PASS" and len(v["report"]) >= 1, v)
    with t.env(CLAUDE_GATE_SLOP="off"):
        t.check("slop-hook: env kill switch", gate("This is fine \u2014 until it isn't, then not.") is None)
    with t.env(CLAUDE_GATES="off"):
        t.check("slop-hook: global env kill switch", gate("This is fine \u2014 until it isn't, then not.") is None)
    off = t.off_file("slop")
    open(off, "w").close()
    t.check("slop-hook: OFF file kill switch", gate("This is fine \u2014 until it isn't, then not.") is None)
    os.remove(off)
    t.check("slop-hook: blocks again once the OFF file is gone", bool(gate("This is fine \u2014 until it isn't.")))
    slop_gate.learn(r"\bsynergy cascade\b", "test pattern", 3.0)
    t.check("slop: a learned pattern scores", slop_gate.analyse("We will build a synergy cascade for the team.")["verdict"] == "FAIL")
    try:
        slop_gate.learn("(", "broken", 1.0)
        t.check("slop: learn rejects a broken regex", False)
    except Exception:
        t.check("slop: learn rejects a broken regex", True)
