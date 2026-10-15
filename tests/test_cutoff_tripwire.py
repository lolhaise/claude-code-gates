import _gate
import cutoff_tripwire as cut

STALE = ("GPT-5 currently supports a 400k context window and it costs $10 per "
         "million output tokens, so the cheap path is batching.")
CASUAL = ("The bakery on the corner was out of rye again, so I got the sourdough "
          "instead and it was honestly better.")
BLOCK = ('{"stale_world_claim": true, "quote_stale": "GPT-5 currently supports a 400k context window"}')
ALLOW = '{"stale_world_claim": false, "quote_stale": ""}'
SPEC0 = ("It is a 10,000-person company employing 250 people in that "
         "division alone, so the pitch was aimed much higher.")


def run(t):
    def c(msg, verdict, uses=(), prompt="hello there", active=False):
        with t.env(CLAUDE_GATES_JUDGE_FAKE=verdict):
            return cut.check(t.event(msg, uses, prompt=prompt, active=active))

    t.check("cutoff: topic gate HITS a stale world claim", bool(cut.DOMAIN_RX.search(STALE)))
    t.check("cutoff: topic gate MISSES casual talk", not cut.DOMAIN_RX.search(CASUAL))
    r = c(STALE, BLOCK)
    t.check("cutoff: no evidence + stale claim BLOCKS", bool(r) and "[cutoff tripwire]" in r, r)
    t.check("cutoff: reason carries the quote", bool(r) and "400k context window" in r, r)
    t.check("cutoff: reason offers the unverified escape", bool(r) and "unverified training-data" in r, r)
    t.check("cutoff: judge says no ALLOWS", c(STALE, ALLOW) is None)
    n0 = _gate.JUDGE_CALLS[0]
    t.check("cutoff: casual reply never judged", c(CASUAL, BLOCK) is None and _gate.JUDGE_CALLS[0] == n0)
    t.check("cutoff: short message never judged", c("GPT-5 costs $10", BLOCK) is None)
    t.check("cutoff: stop_hook_active never loops", c(STALE, BLOCK, active=True) is None)
    t.check("cutoff: judge garbage FAILS OPEN", c(STALE, "not json at all") is None)
    t.check("cutoff: judge timeout marker FAILS OPEN", c(STALE, "__TIMEOUT__") is None)
    with t.env(CLAUDE_GATES_JUDGE=None, PATH=t.tmp):
        t.check("cutoff: no claude binary FAILS OPEN", cut.check(t.event(STALE)) is None)
    with t.env(CLAUDE_GATE_CUTOFF="off"):
        t.check("cutoff: env kill switch", c(STALE, BLOCK) is None)

    for label, uses in (("WebSearch", [("WebSearch", {"query": "gpt-5 context window"})]),
                        ("WebFetch", [("WebFetch", {"url": "https://example.com/pricing"})]),
                        ("curl", [("Bash", {"command": "curl -s https://api.example.com/models"})]),
                        ("an MCP search tool", [("mcp__search__web_search", {"query": "x"})]),
                        ("an MCP browser", [("mcp__browser__navigate_page", {"url": "https://x.example"})])):
        t.check("cutoff: world evidence via %s skips the judge" % label, c(STALE, BLOCK, uses) is None)
    t.check("cutoff: a local systemctl read is NOT world evidence",
            bool(c(STALE, BLOCK, [("Bash", {"command": "systemctl --user list-timers"})])))
    t.check("cutoff: a Write is never evidence",
            bool(c(STALE, BLOCK, [("Write", {"file_path": "/tmp/x.md", "content": "curl https://x"})])))
    with t.env(CLAUDE_GATE_CUTOFF_DOCS="/opt/docs-mirror"):
        t.check("cutoff: a read under CLAUDE_GATE_CUTOFF_DOCS is evidence",
                c(STALE, BLOCK, [("Read", {"file_path": "/opt/docs-mirror/models.md"})]) is None)

    t.check("cutoff: the specificity sentence has ZERO vocabulary hits", not cut.DOMAIN_RX.search(SPEC0))
    t.check("cutoff: gate OPENS on specificity alone", bool(cut.specificity_hits(SPEC0, "")))
    t.check("cutoff: a zero-vocabulary specific claim BLOCKS", "[cutoff tripwire]" in (c(SPEC0, BLOCK) or ""))
    t.check("cutoff: a number the USER typed is not gated",
            c(SPEC0, BLOCK, prompt="is it true they are a 10,000-person company with 250 people in that division") is None)
    t.check("cutoff: a proper noun the user typed is subtracted",
            not cut.specificity_hits("Sam Altman said that last week.", "what did sam altman say"))
    t.check("cutoff: a DIFFERENT name is not subtracted",
            bool(cut.specificity_hits("Sundar Pichai said that last week.", "what did sam altman say")))
    t.check("cutoff: code fences and URLs are not specifics",
            not cut.specificity_hits("Run `npm i -g tool-2.1.0` then read https://example.com/v3.2/notes for it.", ""))
    t.check("cutoff: ordinary chat stays quiet",
            not cut.specificity_hits("yeah that sounds right, lets just do it that way then and see what happens", ""))
    t.check("cutoff: a quotation needs FOUR words", not cut.specificity_hits('He said "nope" and left the room.', ""))
    t.check("cutoff: unreadable transcript FAILS OPEN",
            cut.check({"last_assistant_message": STALE, "transcript_path": "/nonexistent"}, verdict={"stale_world_claim": True}) is None)
