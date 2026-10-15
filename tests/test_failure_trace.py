import json
import os

import failure_trace


def run(t):
    ev = {"cwd": "/work/repo", "tool_name": "Bash", "session_id": "s1",
          "tool_input": {"command": "curl -H 'Authorization: Bearer abc.def.ghi' https://api.example.com/x"},
          "error": "Exit code 7\ncurl: (7) Failed to connect"}
    row = failure_trace.record(ev)
    t.check("trace: a row is written", bool(row))
    with open(failure_trace.trace_path()) as f:
        last = json.loads(f.read().splitlines()[-1])
    t.check("trace: row carries ts, cwd, tool, input, error",
            set(last) == {"ts", "cwd", "tool", "input", "error"} and last["tool"] == "Bash", last)
    t.check("trace: a bearer token is redacted", "abc.def.ghi" not in last["input"] and "<redacted>" in last["input"], last)
    t.check("trace: the error text is kept", "Failed to connect" in last["error"])
    t.check("trace: the file is private (0600)", (os.stat(failure_trace.trace_path()).st_mode & 0o777) == 0o600)
    big = failure_trace.record({"tool_name": "Read", "tool_input": {"x": "y" * 5000}, "error": "e" * 5000})
    t.check("trace: input and error are capped", len(big["input"]) <= 300 and len(big["error"]) <= 500)
    custom = t.path("custom-trace.jsonl")
    with t.env(CLAUDE_GATE_TRACE_FILE=custom):
        failure_trace.record(ev)
        t.check("trace: CLAUDE_GATE_TRACE_FILE redirects the trace", os.path.exists(custom))
    with t.env(CLAUDE_GATE_TRACE="off"):
        t.check("trace: env kill switch writes nothing", failure_trace.record(ev) is None)
