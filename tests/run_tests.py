#!/usr/bin/env python3
"""Run every gate's tests: python3 tests/run_tests.py

Offline and isolated: HOME and the gates' state dir point at a temp folder, the judge is
always faked (no model call, no token), and nothing outside the temp folder is written.
Prints one line per case and the pass count; exits 1 if any case fails.
"""
import importlib
import json
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks")

TMP = tempfile.mkdtemp(prefix="gates-test-")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["CLAUDE_GATES_STATE"] = os.path.join(TMP, "state")
os.environ["CLAUDE_GATES_JUDGE"] = "off"          # a test that wants a verdict fakes one
for k in list(os.environ):
    if (k.startswith("CLAUDE_GATE_") or k in ("CLAUDE_GATES", "CLAUDE_GATES_JUDGE_FAKE",
                                              "CLAUDE_GATES_JUDGE_CHILD", "CLAUDE_GATES_WEBHOOK_URL",
                                              "CLAUDE_GATES_NOTIFY_FILE", "CLAUDE_CONFIG_DIR")):
        os.environ.pop(k)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, HOOKS)
sys.path.insert(0, HERE)


class T(object):
    """The tiny harness every test module receives."""

    hooks = HOOKS
    tmp = TMP

    def __init__(self):
        self.n, self.fails, self._k = 0, [], 0

    def check(self, cid, cond, detail=""):
        self.n += 1
        if cond:
            print("ok   %s" % cid)
        else:
            self.fails.append(cid)
            print("FAIL %-55s %s" % (cid, str(detail)[:200]))

    def path(self, name):
        self._k += 1
        return os.path.join(TMP, "%d-%s" % (self._k, name))

    def transcript(self, tool_uses=(), prompt="hello there", before=()):
        """A transcript: optional earlier rows, one human prompt, then these tool calls,
        each followed by its tool_result row, exactly as the CLI writes them."""
        p = self.path("t.jsonl")
        rows = list(before) + [{"type": "user", "timestamp": "2026-01-01T10:00:00.000Z",
                                "message": {"content": prompt}}]
        for name, inp in tool_uses:
            rows.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": name, "input": inp}]}})
            rows.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "content": "ok"}]}})
        with open(p, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return p

    def event(self, msg, tool_uses=(), prompt="hello there", active=False, **kw):
        ev = {"session_id": "test", "last_assistant_message": msg, "stop_hook_active": active,
              "transcript_path": self.transcript(tool_uses, prompt), "cwd": TMP}
        ev.update(kw)
        return ev

    def env(self, **kv):
        return _Env(kv)

    def off_file(self, name):
        d = os.path.join(os.environ["CLAUDE_GATES_STATE"], "off")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, name)


class _Env(object):
    """Set (or, with None, unset) environment variables for one with-block."""

    def __init__(self, kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in self.kv}
        self._apply(self.kv)
        return self

    def __exit__(self, *exc):
        self._apply(self.old)
        return False

    def _apply(self, kv):
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main():
    t = T()
    mods = sorted(f[:-3] for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    for m in mods:
        print("# %s" % m)
        try:
            importlib.import_module(m).run(t)
        except Exception as e:
            t.check("%s-ran-without-exception" % m, False, repr(e))
    shutil.rmtree(TMP, ignore_errors=True)
    print("\n%d/%d passed" % (t.n - len(t.fails), t.n))
    if t.fails:
        print("failed: " + ", ".join(t.fails))
    return 1 if t.fails else 0


if __name__ == "__main__":
    sys.exit(main())
