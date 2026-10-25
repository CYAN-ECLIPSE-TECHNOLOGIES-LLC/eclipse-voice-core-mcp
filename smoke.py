#!/usr/bin/env python3
"""End-to-end stdio smoke for Eclipse Voice Core v0.1 (V0).

Temp state. Drives initialize -> consent_record_write ->
consent_check ALLOW -> send_evaluate ALLOW (receipt carries the
consent_record_id) -> consent_revoke -> send_evaluate DENY with a
violation record and no send receipt -> receipt_list chain valid.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
AT = "2026-10-25T17:00:00Z"


def rpc(proc, rid, method, params=None):
    req = {"jsonrpc": "2.0", "id": rid, "method": method,
           "params": params or {}}
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def call(proc, rid, name, args):
    response = rpc(proc, rid, "tools/call",
                   {"name": name, "arguments": args})
    return json.loads(response["result"]["content"][0]["text"])


def main():
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env["ECLIPSE_VOICE_CORE_STATE"] = str(Path(tmp) / "state.json")
        proc = subprocess.Popen(
            [sys.executable, str(HERE / "server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            cwd=str(HERE), env=env)
        try:
            init = rpc(proc, 1, "initialize",
                       {"protocolVersion": "2025-03-26"})
            assert init["result"]["serverInfo"]["name"] == \
                "eclipse-voice-core"
            print("initialize OK")

            numbers = call(proc, 2, "number_list", {})
            assert numbers["count"] == 3
            print("number_list OK; 3 fixture numbers enrolled")

            written = call(proc, 3, "consent_record_write", {
                "subject": "contact_smoke", "channel": "voice+sms",
                "scope": "marketing",
                "purpose": "Product updates, per the signup form",
                "source": "web_form",
                "captured_at": "2026-10-01T00:00:00Z",
                "provenance": {"form_id": "signup-2026-10"},
                "jurisdiction_hint": "US-CA"})
            cid = written["consent"]["consent_id"]
            print("consent_record_write OK;", cid)

            check = call(proc, 4, "consent_check", {
                "subject": "contact_smoke", "channel": "sms",
                "scope": "marketing", "at_time": AT})
            assert check["decision"] == "ALLOW"
            print("consent_check OK; ALLOW inside CA window")

            mandate = {"mandate_id": "m-smoke", "cap": 5.0,
                       "platforms": ["twilio"],
                       "approval_threshold": 1.0,
                       "expires_at": "2026-12-31T00:00:00Z"}
            verdict = call(proc, 5, "send_evaluate", {
                "channel": "sms", "from_ref": "num_cetech_main",
                "subject": "contact_smoke", "scope": "marketing",
                "platform": "twilio", "mandate": mandate,
                "estimated_cost": 0.05, "at_time": AT})
            assert verdict["decision"] == "ALLOW"
            assert verdict["consent_record_id"] == cid
            print("send_evaluate OK; ALLOW, receipt carries consent id")

            call(proc, 6, "consent_revoke", {
                "subject": "contact_smoke",
                "revocation_source": "opt_out_keyword"})
            print("consent_revoke OK")

            denied = call(proc, 7, "send_evaluate", {
                "channel": "sms", "from_ref": "num_cetech_main",
                "subject": "contact_smoke", "scope": "marketing",
                "platform": "twilio", "mandate": mandate,
                "estimated_cost": 0.05, "at_time": AT})
            assert denied["decision"] == "DENY"
            assert denied["stage"] == "consent"
            assert "violation_receipt_seq" in denied
            print("send_evaluate after revoke OK; DENY + violation")

            receipts = call(proc, 8, "receipt_list", {})
            assert receipts["chain_valid"] is True
            types = [r["type"] for r in receipts["receipts"]]
            assert types.count("send_authorized") == 1
            assert types.count("violation") == 1
            print(f"receipts OK; {receipts['count']} entries, "
                  "chain valid, exactly one send + one violation")
            print("SMOKE PASSED")
        finally:
            proc.stdin.close()
            proc.terminate()


if __name__ == "__main__":
    main()
