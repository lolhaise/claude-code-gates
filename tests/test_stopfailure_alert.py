import contextlib
import http.server
import io
import json
import os
import threading

import _gate
import stopfailure_alert as sf


def _ledger():
    with open(sf.ledger_path()) as f:
        return [json.loads(l) for l in f.read().splitlines()]


def run(t):
    def fire(error="rate_limit", details="429 Too Many Requests", ts="1000000"):
        buf = io.StringIO()
        with t.env(CLAUDE_GATE_STOPFAIL_NOW=ts), contextlib.redirect_stdout(buf):
            disp = sf.handle({"hook_event_name": "StopFailure", "session_id": "abcdef123456",
                              "error": error, "error_details": details,
                              "last_assistant_message": "API Error: Rate limit reached"})
        return disp, buf.getvalue()

    disp, out = fire()
    t.check("stopfail: first failure alerts on stdout", disp == "stdout" and "[turn failed] rate_limit" in out, (disp, out))
    rows = _ledger()
    t.check("stopfail: ledger row written with the error", rows[-2]["event"] == "turn-failure" and rows[-2]["error"] == "rate_limit")
    t.check("stopfail: the alert's fate is ledgered too", rows[-1]["event"] == "turn-failure-alert" and rows[-1]["result"] == "stdout")
    disp, out = fire(ts="1000060")
    t.check("stopfail: a repeat inside 15 min is suppressed", disp.startswith("suppressed:") and out == "", disp)
    disp, out = fire(ts="1000120")
    t.check("stopfail: suppressed alerts are counted", disp == "suppressed:2", disp)
    disp, out = fire(ts=str(1000000 + 16 * 60))
    t.check("stopfail: after the window it alerts and says how many it ate",
            disp == "stdout" and "suppressed 2 since last" in out, out)
    disp, _ = fire(error="overloaded", ts=str(1000000 + 16 * 60 + 5))
    t.check("stopfail: a different error type has its own window", disp == "stdout", disp)
    disp, out = fire(error="server_error", details="token sk-abcdefghijklmnop1234 leaked", ts="2000000")
    t.check("stopfail: secrets are redacted in the alert", "sk-abcdefghijklmnop" not in out and "<redacted>" in out, out)
    t.check("stopfail: secrets are redacted in the ledger", "sk-abcdefghijklmnop" not in json.dumps(_ledger()[-2]))

    nf = t.path("alerts.log")
    with t.env(CLAUDE_GATES_NOTIFY_FILE=nf):
        disp, _ = fire(error="billing_error", ts="3000000")
    t.check("stopfail: CLAUDE_GATES_NOTIFY_FILE gets the alert line",
            disp == "file:ok" and "billing_error" in open(nf).read(), disp)
    with t.env(CLAUDE_GATES_NOTIFY_FILE="/proc/nope/alerts.log"):
        disp, _ = fire(error="max_output_tokens", ts="4000000")
        disp2, _ = fire(error="max_output_tokens", ts="4000010")
    t.check("stopfail: a failed delivery does not buy silence", disp.startswith("file:") and disp2.startswith("file:"),
            (disp, disp2))
    with t.env(CLAUDE_GATE_STOPFAIL="off"):
        n = len(_ledger())
        disp, out = fire(error="unknown", ts="5000000")
    t.check("stopfail: OFF silences the alert but the ledger still gets the row",
            disp == "off" and out == "" and len(_ledger()) == n + 2)

    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.handle_request)
    th.start()
    with t.env(CLAUDE_GATES_WEBHOOK_URL="http://127.0.0.1:%d/hook" % srv.server_port):
        ok, disp = _gate.notify_sync("[turn failed] test", allow_stdout=True)
    th.join(10)
    srv.server_close()
    t.check("stopfail: the webhook gets JSON with both text and content",
            ok and got and got[0].get("text") == "[turn failed] test" and got[0].get("content") == "[turn failed] test",
            (ok, disp, got))
    with t.env(CLAUDE_GATES_WEBHOOK_URL="http://127.0.0.1:9/nothing-listens"):
        ok, disp = _gate.notify_sync("x")
    t.check("stopfail: an unreachable webhook fails without raising", ok is False and "failed" in disp, disp)
