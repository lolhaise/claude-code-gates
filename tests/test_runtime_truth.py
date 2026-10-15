import runtime_truth as rt

CLAIM = ("Good news: hooks hot-reload on their own, so the new Stop hook is live the second "
         "you save settings.json, no restart needed.")
CASUAL = "The bakery on the corner was out of rye again, so I got the sourdough instead."
BLOCK = '{"mechanism_claim": true, "quote_mechanism": "hooks hot-reload on their own"}'
ALLOW = '{"mechanism_claim": false, "quote_mechanism": ""}'


def run(t):
    def trip(msg, verdict, uses=(), active=False):
        with t.env(CLAUDE_GATES_JUDGE_FAKE=verdict):
            return rt.check(t.event(msg, uses, active=active))

    t.check("runtime: domain gate HITS tool talk", bool(rt.DOMAIN_RX.search(CLAIM)))
    t.check("runtime: domain gate MISSES casual talk", not rt.DOMAIN_RX.search(CASUAL))
    t.check("runtime: domain gate is word-boundary ('hook' not inside 'hookah')",
            not rt.DOMAIN_RX.search("the hookah lounge closed early and everyone went home"))
    r = trip(CLAIM, BLOCK)
    t.check("runtime: no evidence + claim BLOCKS", bool(r) and "[runtime-truth tripwire]" in r, r)
    t.check("runtime: reason carries the quote", bool(r) and "hot-reload on their own" in r, r)
    t.check("runtime: judge says no ALLOWS", trip(CLAIM, ALLOW) is None)
    t.check("runtime: casual reply never judged", trip(CASUAL, BLOCK) is None)
    t.check("runtime: stop_hook_active never loops", trip(CLAIM, BLOCK, active=True) is None)
    t.check("runtime: judge garbage FAILS OPEN", trip(CLAIM, "</>") is None)
    for label, uses in (("Read of settings.json", [("Read", {"file_path": "/Users/u/.claude/settings.json"})]),
                        ("cat of settings.local.json", [("Bash", {"command": "cat .claude/settings.local.json"})]),
                        ("claude --help", [("Bash", {"command": "claude --help | grep -i hook"})]),
                        ("a docs fetch", [("WebFetch", {"url": "https://docs.claude.com/en/docs/claude-code/hooks"})]),
                        ("a read of CLAUDE.md", [("Read", {"file_path": "/repo/CLAUDE.md"})]),
                        ("a read of this repo's own hook source", [("Read", {"file_path": rt.HOOKS_DIR + "/_gate.py"})])):
        t.check("runtime: evidence via %s skips the judge" % label, trip(CLAIM, BLOCK, uses) is None)
    t.check("runtime: a web search does NOT satisfy it",
            bool(trip(CLAIM, BLOCK, [("WebSearch", {"query": "claude code hooks reload"})])))
    t.check("runtime: a Write of settings.json is not a read",
            bool(trip(CLAIM, BLOCK, [("Write", {"file_path": "/Users/u/.claude/settings.json", "content": "{}"})])))
    with t.env(CLAUDE_GATE_RUNTIME_DOCS="/srv/cc-docs"):
        t.check("runtime: a read under CLAUDE_GATE_RUNTIME_DOCS is evidence",
                trip(CLAIM, BLOCK, [("Bash", {"command": "grep -rn reload /srv/cc-docs/hooks.md"})]) is None)
    with t.env(CLAUDE_GATE_RUNTIME="off"):
        t.check("runtime: env kill switch", trip(CLAIM, BLOCK) is None)
