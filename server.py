#!/usr/bin/env python3
"""Eclipse Voice Core MCP — v0.1.0, phase V0 (Consent Registry +
Number Registry).

Powered by Cyan Eclipse Technologies (cetech.ai).

Descriptor: Eclipse Voice Core — the Consent Registry and Number
Registry every Eclipse telephony adapter obeys.

Family descriptor: one Consent Registry and Number Registry
govern every call and text across carriers, business phone
systems, and AI voice platforms. No adapter dials or sends
without a consent record with provenance and an Agent Wallet
mandate; revocation propagates to every adapter instantly. It
never touches emergency services, never spoofs a caller ID,
never fabricates consent, and never lets an AI voice pretend to
be a human.

This server owns (spec eclipse-voice-family-spec-20261025
sections 5-8):
  * the Consent Registry — provenance-bound records (a record
    without provenance is VOID), one-way revocation that
    propagates at check time, DNC overlays + calling windows as
    versioned data with provenance;
  * the Number Registry — enrolled numbers only (unenrolled =
    nonexistent), per-number registration state (A2P 10DLC,
    toll-free verification, STIR/SHAKEN, branded ID) deciding
    sendability per channel/scope;
  * the send gate — consent first, then the Wallet-shaped
    mandate; a receipt carrying a consent_record_id is writable
    only when consent ALLOWs, otherwise the attempt produces a
    violation record and nothing else (spec 8.3);
  * the family receipt ledger — append-only, hash-chained.

ADAPTER SEAM (V0 fixture form, stated plainly): adapters import
core_engine from this directory and evaluate against the SHARED
family state file, loaded fresh at every call. Adapters hold no
consent copies, so a revocation lands everywhere at the next
check. The production form is a live registry service behind
this same tool surface (V1, behind the owner Twilio account +
counsel gate).

V0 SCOPE: fixtures only. No carrier account, no real numbers, no
calls placed, no texts sent, and no certification claimed —
bench pending.

Transport: MCP over stdio (newline-delimited JSON-RPC 2.0),
stdlib only. State: $ECLIPSE_VOICE_CORE_STATE or
./data/state.json beside this file, seeded from fixtures.json.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import core_engine as eng  # noqa: E402
from core_engine import ToolError  # noqa: E402

STATE_PATH = Path(os.environ.get(
    "ECLIPSE_VOICE_CORE_STATE", HERE / "data" / "state.json"))
FIXTURES_PATH = HERE / "fixtures.json"


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    state = eng.empty_state()
    seed = json.loads(FIXTURES_PATH.read_text())
    state["config"].update(seed.get("config", {}))
    state["dnc_lists"] = seed.get("dnc_lists", [])
    state["windows"] = seed.get("windows", [])
    for rec in seed.get("numbers", []):
        state["numbers"][rec["number_id"]] = rec
    save_state(state)
    return state


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1))


# ------------------------------------------------------------------- tools
def t_consent_check(state, args):
    return eng.consent_check(state, args["subject"], args["channel"],
                             args["scope"], args.get("at_time"))


def t_consent_record_write(state, args):
    return eng.consent_record_write(state, args)


def t_consent_revoke(state, args):
    return eng.consent_revoke(state, args)


def t_consent_history(state, args):
    return eng.consent_history(state, args["subject"])


def t_dnc_status(state, args):
    return eng.dnc_status(state, args.get("subject"),
                          args.get("at_time"))


def t_window_check(state, args):
    return eng.window_check(state, args.get("jurisdiction"),
                            args["channel"], args["scope"],
                            args.get("at_time"))


def t_number_list(state, args):
    return {"numbers": [eng.number_public(r)
                        for r in state["numbers"].values()],
            "count": len(state["numbers"])}


def t_number_get(state, args):
    rec = eng.find_number(state, args["number_ref"])
    if rec is None:
        raise ToolError(
            f"NUMBER_NOT_ENROLLED: {args['number_ref']} does not "
            "exist to this family (spec 6)")
    return {"number": eng.number_public(rec)}


def t_number_enroll(state, args):
    return eng.number_enroll(state, args)


def t_number_health_report(state, args):
    if args.get("number_id") and args["number_id"] not in state["numbers"]:
        raise ToolError(f"NUMBER_NOT_ENROLLED: {args['number_id']}")
    return eng.number_health_report(state, args.get("number_id"))


def t_send_evaluate(state, args):
    return eng.send_evaluate(
        state, channel=args["channel"], from_ref=args["from_ref"],
        subject=args["subject"], scope=args["scope"],
        platform=args.get("platform", "twilio"),
        mandate=args.get("mandate"),
        estimated_cost=args.get("estimated_cost", 0),
        at_time=args.get("at_time"),
        confirm_token=args.get("confirm_token"))


def t_activity_emit(state, args):
    return eng.activity_emit(state, args)


def t_kill_switch(state, args):
    engaged = bool(args.get("engaged"))
    state["config"]["family_kill_switch"] = engaged
    receipt = eng.emit_receipt(state, "kill_switch_set",
                               {"engaged": engaged, "scope": "family"})
    return {"family_kill_switch": engaged,
            "receipt_seq": receipt["seq"]}


def t_receipt_list(state, args):
    limit = args.get("limit")
    receipts = state["receipts"]
    if limit is not None:
        receipts = receipts[-int(limit):]
    return {"receipts": receipts, "count": len(state["receipts"]),
            "chain_valid": eng.verify_receipt_chain(state["receipts"])}


TOOLS = {
    "consent_check": (
        t_consent_check,
        "T0. The single consent door (spec 5): (subject, channel, "
        "scope, at_time) -> ALLOW/DENY + reason + consent_record_id, "
        "with DNC and window sub-verdicts. A record without "
        "provenance is void and never authorizes.",
        {"subject": {"type": "string"}, "channel": {"type": "string"},
         "scope": {"type": "string"}, "at_time": {"type": "string"}},
        ["subject", "channel", "scope"]),
    "consent_record_write": (
        t_consent_record_write,
        "T1. Write a NEW consent record. Provenance is REQUIRED — "
        "a record without it is void, refused, and receipted as a "
        "violation (spec 5.2). Never edits history.",
        {"subject": {"type": "string"}, "channel": {"type": "string"},
         "scope": {"type": "string"}, "purpose": {"type": "string"},
         "source": {"type": "string"},
         "captured_at": {"type": "string"},
         "provenance": {"type": "object"},
         "method_detail": {"type": "object"},
         "jurisdiction_hint": {"type": "string"},
         "recorded_by": {"type": "string"}},
        ["subject", "channel", "scope", "purpose", "source",
         "captured_at", "provenance"]),
    "consent_revoke": (
        t_consent_revoke,
        "T1. Revoke consent (one consent_id, or every active record "
        "for a subject). Total and one-way (spec 5.3): re-consent "
        "requires a new record with new provenance. Receipted.",
        {"consent_id": {"type": "string"},
         "subject": {"type": "string"},
         "revocation_source": {"type": "string"}},
        ["revocation_source"]),
    "consent_history": (
        t_consent_history,
        "T0. Full consent trail for a subject: records with their "
        "append-only status history.",
        {"subject": {"type": "string"}}, ["subject"]),
    "dnc_status": (
        t_dnc_status,
        "T0. DNC overlay datasets: versions, provenance, freshness; "
        "optionally one subject's match state (spec 5.4).",
        {"subject": {"type": "string"}, "at_time": {"type": "string"}},
        []),
    "window_check": (
        t_window_check,
        "T0. Calling-window evaluation from jurisdiction rule data "
        "(spec 5.4): inside/outside + the rule cited. Unknown "
        "timezone = outside = DENY.",
        {"jurisdiction": {"type": "string"},
         "channel": {"type": "string"}, "scope": {"type": "string"},
         "at_time": {"type": "string"}},
        ["channel", "scope"]),
    "number_list": (
        t_number_list,
        "T0. Every enrolled number with its registration state and "
        "per-channel/scope sendability matrix. Unenrolled numbers "
        "do not exist to this family (spec 6).",
        {}, []),
    "number_get": (
        t_number_get,
        "T0. One enrolled number by number_id or E.164. Unenrolled "
        "refs are refused as nonexistent.",
        {"number_ref": {"type": "string"}}, ["number_ref"]),
    "number_enroll": (
        t_number_enroll,
        "T1 operator enrollment (V0 fixture form): register a "
        "number with capabilities + registration state. Live "
        "provisioning is an adapter T2 surface at V1.",
        {"e164": {"type": "string"}, "number_id": {"type": "string"},
         "platform": {"type": "string"},
         "account_ref": {"type": "string"},
         "capabilities": {"type": "array",
                          "items": {"type": "string"}},
         "owner_purpose": {"type": "string"},
         "registration": {"type": "object"},
         "spam_labels": {"type": "array",
                         "items": {"type": "object"}},
         "status": {"type": "string"}}, ["e164"]),
    "number_health_report": (
        t_number_health_report,
        "T0. Spam-label observations + registration state + what "
        "is send-blocked and why, per number or fleet-wide (spec "
        "6). Reporting only — no evasion advice exists here.",
        {"number_id": {"type": "string"}}, []),
    "send_evaluate": (
        t_send_evaluate,
        "The two-gate send evaluation (spec 8): live Consent "
        "Registry check FIRST, then the Agent Wallet-shaped "
        "mandate (cap, platform + destination allowlists, "
        "approval threshold, expiry), plus Number Registry "
        "sendability. ALLOW returns the consent_record_id the "
        "send receipt must carry; an unconsented attempt writes "
        "a violation record and nothing else (spec 8.3). "
        "Executes nothing.",
        {"channel": {"type": "string"}, "from_ref": {"type": "string"},
         "subject": {"type": "string"}, "scope": {"type": "string"},
         "platform": {"type": "string"},
         "mandate": {"type": "object"},
         "estimated_cost": {"type": "number"},
         "at_time": {"type": "string"},
         "confirm_token": {"type": "string"}},
        ["channel", "from_ref", "subject", "scope"]),
    "activity_emit": (
        t_activity_emit,
        "T1. The emission door into the Customer Graph (spec "
        "11.2): normalized call/sms/voicemail activity, references "
        "not media. Blocked attempts emit too (blocked_consent / "
        "blocked_window are first-class dispositions).",
        {"kind": {"type": "string"}, "direction": {"type": "string"},
         "occurred_at": {"type": "string"},
         "parties": {"type": "object"},
         "platform_ref": {"type": "object"},
         "duration_s": {"type": "number"},
         "disposition": {"type": "string"},
         "recording_ref": {"type": "string"},
         "transcript_ref": {"type": "string"},
         "consent_ref": {"type": "string"},
         "receipt_ref": {"type": "string"}},
        ["kind", "direction", "occurred_at", "platform_ref",
         "disposition"]),
    "kill_switch_set": (
        t_kill_switch,
        "T1. The family-wide kill switch: freezes every T2+ send "
        "evaluation at DENY while T0 history stays readable "
        "(spec 7). Receipted.",
        {"engaged": {"type": "boolean"}}, ["engaged"]),
    "receipt_list": (
        t_receipt_list,
        "T0. The family ledger: append-only, hash-chained "
        "receipts incl. violation records; verifies the chain.",
        {"limit": {"type": "integer"}}, []),
}

# Capability denylist, stated in code (spec 10): this core holds
# no emergency-services surface, no caller-ID surface, and no
# consent-editing surface at any tier.
FORBIDDEN_TOOL_NAMES = {
    "emergency_call", "e911_config", "e911_address_write",
    "test_911", "caller_id_set", "caller_id_spoof",
    "display_name_set", "consent_edit", "consent_unrevoke",
    "consent_fabricate", "dnc_remove", "number_release",
}


def tool_list():
    return [{"name": name, "description": description,
             "inputSchema": {"type": "object", "properties": props,
                             "required": required}}
            for name, (_, description, props, required) in TOOLS.items()]


def handle(req: dict):
    method, rid = req.get("method", ""), req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": req.get("params", {}).get(
                "protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "eclipse-voice-core",
                           "version": "0.1.0"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"tools": tool_list()}}
    if method == "tools/call":
        name = req["params"]["name"]
        args = req["params"].get("arguments", {})
        if name in FORBIDDEN_TOOL_NAMES or name not in TOOLS:
            return {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32602,
                "message": f"unknown or line-forbidden tool {name}"}}
        state = load_state()
        receipts_before = len(state["receipts"])
        try:
            out = TOOLS[name][0](state, args)
            save_state(state)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps(out, indent=1)}]}}
        except ToolError as exc:
            # A refused write may still have receipted a violation
            # (consent_record_write, spec 5.2): persist exactly that
            # growth, nothing else.
            if len(state["receipts"]) > receipts_before:
                save_state(state)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps({"error": str(exc)})}],
                "isError": True}}
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32601, "message": f"method not found: {method}"}}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            response = handle(json.loads(line))
        except Exception as exc:  # never crash the transport
            response = {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32603, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
