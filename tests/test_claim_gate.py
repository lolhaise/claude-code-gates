import claim_gate as cg


def run(t):
    def g(msg, uses=(), active=False):
        return cg.check(t.event(msg, uses, active=active))

    for cid, txt, tier in (("I sent", "Done, I sent the report to the team channel.", "send"),
                           ("I've emailed", "I've emailed them the signed copy.", "send"),
                           ("it has been posted", "So it has been posted to the board now.", "send"),
                           ("I ran", "Yeah, I ran the tests and they pass.", "work"),
                           ("I checked", "I checked the logs, nothing unusual there.", "work"),
                           ("I looked it up", "I looked it up and it opens at nine.", "work")):
        t.check("claim: scan finds '%s'" % cid, cg.claim_scan(txt)[0] == tier, cg.claim_scan(txt))
    for cid, txt in (("intent", "I'll send it once you confirm the address."),
                     ("offer", "Want me to run the tests first?"),
                     ("negation", "I haven't sent it yet, it needs your sign-off."),
                     ("hypothetical", "If I checked the logs now it would take an hour."),
                     ("reported speech", "You said I ran it twice, that is not what happened."),
                     ("past turn", "Yesterday I ran the full suite and it passed."),
                     ("relative clause", "Open the file I sent you and go to page two."),
                     ("reply is the artifact", "Here's the draft, I wrote it out below."),
                     ("quoted draft", 'Your email can say: "I sent the invoice on Monday, please confirm."'),
                     ("code", "Run `I ran the tests` as the commit message."),
                     ("plain answer", "The function returns None when the file is missing.")):
        t.check("claim: no claim in %s" % cid, cg.claim_scan(txt)[0] is None, cg.claim_scan(txt))
    r = g("Yeah, I ran the tests and they pass.")
    t.check("claim: an action claim with NO tool call this turn BLOCKS", bool(r) and "[claim gate]" in r, r)
    t.check("claim: the reason quotes the sentence", "I ran the tests" in (r or ""), r)
    t.check("claim: any tool call this turn clears it",
            g("Yeah, I ran the tests and they pass.", [("Bash", {"command": "pytest -q"})]) is None)
    t.check("claim: stop_hook_active never loops", g("Yeah, I ran the tests and they pass.", active=True) is None)
    t.check("claim: unreadable transcript FAILS OPEN",
            cg.check({"last_assistant_message": "I ran the tests.", "transcript_path": "/nonexistent"}) is None)
    with t.env(CLAUDE_GATE_CLAIM="off"):
        t.check("claim: env kill switch", g("Yeah, I ran the tests and they pass.") is None)
