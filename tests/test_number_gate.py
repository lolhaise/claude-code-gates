import json
import os

import number_gate


def run(t):
    tpath = t.path("num.jsonl")
    with open(tpath, "w") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "the quote is 120k and the run cost $0.405"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"content": "sure, $999k it is"}}) + "\n")
    cwd = t.path("proj")
    os.makedirs(cwd)

    def numg(msg, active=False):
        return number_gate.check({"last_assistant_message": msg, "session_id": "s-num",
                                  "stop_hook_active": active, "transcript_path": tpath, "cwd": cwd})

    r = numg("The contractor quoted $70k for the roof, that is the whole story here.")
    t.check("number: an invented quote BLOCKS", bool(r) and "[number gate]" in r and "$70k" in r, r)
    t.check("number: a figure the user typed ALLOWS", numg("The quote at 120k is the number, and the run cost $0.405.") is None)
    t.check("number: the assistant's own line never grounds", bool(numg("As I said, $999k it is, and that settles it.")))
    t.check("number: code span ignored", numg("The tool prints `$123k` as a placeholder in its usage line.") is None)
    t.check("number: non-money numbers ignored", numg("Six workers, 236,041 files, 12 GB of it, 99% full, a 391 second freeze.") is None)
    t.check("number: stop_hook_active never loops", numg("The contractor quoted $70k for the roof, that is all.", active=True) is None)
    with open(os.path.join(cwd, "CLAUDE.md"), "w") as f:
        f.write("- the storage unit is $2,400 a year\n")
    t.check("number: a figure in ./CLAUDE.md ALLOWS", numg("The storage unit is $2,400 a year and that has not moved.") is None)
    facts = t.path("facts")
    os.makedirs(facts)
    with open(os.path.join(facts, "money.md"), "w") as f:
        f.write("budget: $4,200 per quarter\n")
    with t.env(CLAUDE_GATE_NUMBER_FACTS=facts):
        t.check("number: a figure in a CLAUDE_GATE_NUMBER_FACTS dir ALLOWS",
                numg("The budget is $4,200 a quarter, same as last time we checked.") is None)
    t.check("number: boundary-aware match ('83k' is not inside '183k')",
            not number_gate._match("raised 183k last year", "83k"))
    t.check("number: k-form keys include the long form", "$70,000" in number_gate.money_keys("$70k"))
    with t.env(CLAUDE_GATE_NUMBER="off"):
        t.check("number: env kill switch", numg("The contractor quoted $70k for the roof, that is all.") is None)
    t.check("number: missing transcript PASSES (fail open)",
            number_gate.check({"last_assistant_message": "It costs $5,555 a seat, take it or leave it.",
                               "transcript_path": "/nonexistent", "cwd": cwd}) is None)
