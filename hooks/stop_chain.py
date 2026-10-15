#!/usr/bin/env python3
"""The Stop entry point: every reply gate, in cost order, one bounce at most.

    slop -> claim -> number -> image         free, regex and a transcript read
    runtime-truth + cutoff                   free triage, then ONE shared judge call

Why a chain instead of six separate Stop hooks: Claude Code runs hooks for one event in
parallel, so six independent gates could bounce the same reply six times and spend two
model calls. Here the first gate that fires wins, a judged gate is reached only when every
free gate passed, and both tripwires ride in the same judge prompt.

One bounce per turn: when a gate bounces, the model rewrites, and the next Stop arrives
with stop_hook_active=true, which every gate treats as "stand down". Claude Code also
caps consecutive Stop blocks at 8, so even a broken gate cannot loop forever.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402
import claim_gate  # noqa: E402
import cutoff_tripwire  # noqa: E402
import image_tripwire  # noqa: E402
import number_gate  # noqa: E402
import runtime_truth  # noqa: E402
import slop_gate  # noqa: E402

FREE = (slop_gate, claim_gate, number_gate, image_tripwire)
JUDGED = (runtime_truth, cutoff_tripwire)   # runtime first: the more concrete defect


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def run_chain(event):
    """-> (gate_name, reason) or None. Never raises."""
    try:
        if event.get("stop_hook_active"):
            return None
        turn = _gate.Turn(event)
        for g in FREE:
            r = _safe(g.check, event, turn)
            if r:
                return g.NAME, r
        opened = [g for g in JUDGED if _safe(g.gate_open, event, turn)]
        if not opened:
            return None                       # neither tripwire opened: zero model calls
        verdict = _gate.judge([g.QUESTION for g in opened], _gate.reply_of(event))
        for g in opened:
            r = _safe(g.reason_from_verdict, event, verdict)
            if r:
                return g.NAME, r
        return None
    except Exception:
        return None


def main():
    try:
        if _gate.in_judge_session():
            sys.exit(0)
        ev = _gate.read_event()
        if ev is not None:
            hit = run_chain(ev)
            if hit:
                _gate.emit({"decision": "block", "reason": hit[1]})
    except SystemExit:
        raise
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
