import json
import os

import _gate
import image_tripwire

CLAIM = "I looked everywhere. There are no photos of the old mill's interior online."


def run(t):
    def ig(msg, uses=(), active=False):
        return image_tripwire.check(t.event(msg, uses, prompt="find me photos of the place", active=active))

    r = ig(CLAIM)
    t.check("image: claim with no search BLOCKS", bool(r), r)
    t.check("image: block text has the contract shape",
            (r or "").startswith("[image tripwire] This reply says there are no images of "), r)
    t.check("image: names the subject", "old mill's interior" in (r or ""), r)
    t.check("image: subject drops the locative tail", "online" not in (r or "").split(" and no image")[0], r)
    t.check("image: WebSearch about images satisfies", ig(CLAIM, [("WebSearch", {"query": "old mill interior photos"})]) is None)
    t.check("image: MCP search about images satisfies",
            ig(CLAIM, [("mcp__search__web_search", {"query": "old mill pictures"})]) is None)
    t.check("image: WebFetch of Google Images satisfies",
            ig(CLAIM, [("WebFetch", {"url": "https://www.google.com/search?q=old+mill&tbm=isch"})]) is None)
    t.check("image: curl of Bing Images satisfies",
            ig(CLAIM, [("Bash", {"command": "curl -s 'https://www.bing.com/images/search?q=old+mill'"})]) is None)
    t.check("image: browser batch with a DDG images URL satisfies",
            ig(CLAIM, [("mcp__browser__act", {"actions": [{"url": "https://duckduckgo.com/?q=mill&iax=images&ia=images"}]})]) is None)
    with t.env(CLAUDE_GATE_IMAGE_SEARCH_RX=r"\bmy-img-search\b"):
        t.check("image: a custom search command satisfies",
                ig(CLAIM, [("Bash", {"command": "./my-img-search 'old mill'"})]) is None)
    t.check("image: a plain web search does NOT satisfy", bool(ig(CLAIM, [("WebSearch", {"query": "old mill hours"})])))
    t.check("image: a non-image URL does NOT satisfy",
            bool(ig(CLAIM, [("WebFetch", {"url": "https://example.com/old-mill"})])))
    t.check("image: an unrelated Bash does NOT satisfy", bool(ig(CLAIM, [("Bash", {"command": "ls -la"})])))

    stale = t.path("stale.jsonl")
    with open(stale, "w") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "older turn"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "WebSearch", "input": {"query": "mill photos"}}]}}) + "\n")
        f.write(json.dumps({"type": "user", "message": {"content": "now write it up"}}) + "\n")
    t.check("image: a search from a PREVIOUS turn does not count",
            bool(image_tripwire.check({"last_assistant_message": CLAIM, "transcript_path": stale})))
    t.check("image: the turn reader is bounded to the current turn", _gate.Turn({"transcript_path": stale}).tool_uses() == [])

    for cid, txt in (("no-images-of", "There are no images of the patio."),
                     ("no-photos-for", "no photos for that address, sorry"),
                     ("couldnt-find", "I couldn't find any pictures of it."),
                     ("could-not-find", "I could not find any photos of the room."),
                     ("there-were-no", "there were no images of the dish"),
                     ("nothing-came-up", "Nothing came up for interior images."),
                     ("no-interior-shots", "There are no interior shots available."),
                     ("no-pics-of", "no pics of the bar exist"),
                     ("unable-to-find", "I was unable to find any photos of it.")):
        t.check("image: claim shape " + cid, bool(ig(txt)), txt)
    for cid, txt in (("plain-answer", "The mill is on River Rd, open till 11."),
                     ("images-attached", "Here are four photos of the interior."),
                     ("no-reservations", "There are no reservations left tonight."),
                     ("empty", ""),
                     ("no-images-word-alone", "The gallery has no images tab.")):
        t.check("image: no claim passes " + cid, ig(txt) is None, txt)
    t.check("image: quoting the bounce does not re-fire",
            ig("[image tripwire] This reply says there are no images of X ...") is None)
    t.check("image: a fenced code block is stripped", ig("Pattern:\n```\nno photos of X\n```\nThat is all.") is None)
    t.check("image: inline code is stripped", ig("The regex is `no images of (.*)` and it works.") is None)
    t.check("image: stop_hook_active never loops", ig(CLAIM, active=True) is None)
    with t.env(CLAUDE_GATE_IMAGE="off"):
        t.check("image: env kill switch", ig(CLAIM) is None)
    off = t.off_file("image")
    open(off, "w").close()
    t.check("image: OFF file kill switch", ig(CLAIM) is None)
    os.remove(off)
    bad = t.path("bad.jsonl")
    with open(bad, "w") as f:
        f.write("{not json at all\n\nnope\n<<<garbage>>>\n")
    t.check("image: malformed transcript PASSES (fail open)",
            image_tripwire.check({"last_assistant_message": CLAIM, "transcript_path": bad}) is None)
    t.check("image: missing transcript PASSES (fail open)",
            image_tripwire.check({"last_assistant_message": CLAIM, "transcript_path": "/nonexistent"}) is None)
