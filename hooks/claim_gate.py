#!/usr/bin/env python3
"""Claim gate: "I sent it" / "I ran the tests" with no tool call this turn gets bounced.

A reader cannot tell a confabulated action from a real one: "I checked, it passes" reads
the same whether a test ran or nothing did. This is the mechanical version of "never
report an action as done unless a tool result this turn confirms it".

Free and deterministic, no model call:
  * TENSE is the trick. Intent uses the base form after a modal ("I'll send", "want me
    to check?"); a completed claim uses the past form ("I sent"). Matching the past form
    alone separates intent from claim.
  * The subject must OPEN a clause, which drops the dominant false positive, a relative
    clause pointing back at an artifact ("the file I sent you").
  * Per-sentence suppressors: negation ("I haven't sent it"), hypotheticals and reported
    speech ("if I checked", "you said I ran"), past-turn references ("yesterday I ran"),
    and the artifact being the reply itself ("here is", "below").
  * Quoted text, code and URLs are stripped first: a draft written FOR the user to send
    ("I sent the invoice on Monday") is their line, not an action of the assistant.

Any tool call this turn clears it. An unreadable transcript fails OPEN.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402

NAME = "claim"
QUOTE_CAP = 240

_V_SEND = (r"sent|emailed|e-mailed|mailed|messaged|texted|pinged|dm'?ed|dm'?d|posted"
           r"|submitted|applied(?=\s+(?:to|for)\b)|uploaded|forwarded|filed|booked|ordered"
           r"|scheduled|published|deployed|shipped|notified|invited|registered")
_V_WORK = (r"checked|verified|confirmed|searched|grepped|ran|tested|measured|counted"
           r"|updated|logged|saved|fixed|patched|edited|installed|downloaded|pulled"
           r"|renamed|deleted|rebuilt|regenerated")
_CLAUSE = r"(?:^|[.!?,;:\n]\s*|\b(?:and|but|so|then|plus|also)\s+)"
_I_DID = _CLAUSE + "I(?:'ve|’ve)?\\s+(?:have\\s+)?(?:just\\s+|already\\s+|also\\s+|finally\\s+|then\\s+)?"

SEND_RX = re.compile(r"""(?xi)
    %s(?:%s)\b
  | %s(?:it|that|this|the\s+(?:email|message|note|file|photo|link|invite|form|application))
      \s+(?:is|has\s+been|was|got)\s+(?:sent|emailed|posted|submitted|delivered|uploaded)\b
  | \b(?:email|message|invite|application|form|link)\s+(?:is\s+)?(?:sent|delivered|submitted)\b
  | ^\s*(?:just\s+)?sent\s+(?:it|them|that)\b
""" % (_I_DID, _V_SEND, _CLAUSE))
WORK_RX = re.compile(r"""(?xi)
    %s(?:%s)\b
  | \bI(?:'ve|’ve)?\s+(?:just\s+|already\s+)?looked\s+(?:it\s+|that\s+|them\s+)?up\b
""" % (_I_DID, _V_WORK))
NEG_RX = re.compile(r"""(?xi)
    \b(?:haven(?:'|’)?t|hasn(?:'|’)?t|hadn(?:'|’)?t|didn(?:'|’)?t|don(?:'|’)?t
      |doesn(?:'|’)?t|won(?:'|’)?t|can(?:'|’)?t|cannot|couldn(?:'|’)?t|wouldn(?:'|’)?t
      |shouldn(?:'|’)?t|isn(?:'|’)?t|aren(?:'|’)?t|wasn(?:'|’)?t|never|not)
    \s+(?:\w+\s+){0,3}(?:%s|%s|send|email|post|submit|check|run|apply)\b
""" % (_V_SEND, _V_WORK))
HYPO_RX = re.compile(r"""(?xi)
    \bif\s+I\b | \bwhen\s+I\b | \bhad\s+I\b | \bwould\s+have\b | \bwas\s+going\s+to\b
  | \byou\s+(?:said|asked|think|thought|told|want|wanted)\b
  | \bdid\s+I\b | \bshould\s+I\b | \bwant\s+me\s+to\b | \bdo\s+you\s+want\b
""")
PAST_REF_RX = re.compile(r"""(?xi)
    \b(?:yesterday|last\s+(?:night|week|month|year|time)|this\s+morning|earlier
      |the\s+other\s+(?:day|night)|back\s+(?:in|then)
      |(?:a|one|two|three|four|five|six|ten|\d+)\s+\w+\s+ago|\bago\b
      |on\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b
""")
INREPLY_RX = re.compile(r"""(?xi)
    \b(?:wrote|written)\s+(?:you|it\s+out|up|this)\b
  | \b(?:below|above|here(?:'s|’s)|here\s+is|attached\s+is)\b
""")
QUOTE_RX = re.compile("[\"“]([^\"”\n]{8,300})[\"”]")


def claim_scan(msg):
    """-> (tier, sentence) for the first completed-action claim, or (None, "")."""
    try:
        text = QUOTE_RX.sub(" ", _gate.strip_code(msg))
        for raw in re.split(r"(?<=[.!?])\s+|\n+", text):
            s = raw.strip()
            if len(s) < 3 or NEG_RX.search(s) or HYPO_RX.search(s) or PAST_REF_RX.search(s):
                continue
            if SEND_RX.search(s):
                return "send", s[:QUOTE_CAP]
            if INREPLY_RX.search(s):
                continue
            if WORK_RX.search(s):
                return "work", s[:QUOTE_CAP]
        return None, ""
    except Exception:
        return None, ""


def check(event, turn=None):
    """Stop gate. -> block reason, or None. Never raises."""
    try:
        if event.get("stop_hook_active") or _gate.gate_off(NAME):
            return None
        msg = _gate.reply_of(event).strip()
        if not msg or msg.startswith("NO_REPLY"):
            return None
        tier, quote = claim_scan(msg)
        if not tier:
            return None
        uses = (turn or _gate.Turn(event)).tool_uses()
        if uses is None or uses:
            return None              # unreadable (fail open), or something DID run this turn
        _gate.log_bounce(NAME, event, tier=tier, quote=quote)
        return ("[claim gate] This reply says '%s', a completed action, and no tool ran in this "
                "turn at all. Never report an action as done unless a tool result this turn "
                "confirms it. Either do it now and name the result, or reword it as intent "
                "(\"I'll send it\", \"want me to?\"). If it happened in an earlier turn, say when."
                % quote)
    except Exception:
        return None


if __name__ == "__main__":
    _gate.run_stop_gate(check)
