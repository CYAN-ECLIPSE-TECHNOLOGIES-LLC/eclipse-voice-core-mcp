"""Eclipse Voice Core — shared engine (spec eclipse-voice-family-
spec-20261025 sections 5-8).

This module is the family law in code: the Consent Registry, the
DNC/window overlays, the Number Registry, the two-gate send
evaluation (consent first, then the Agent Wallet-shaped mandate),
and the append-only hash-chained family receipt ledger.

Adapters (Trunk first) import this module from the sibling core
directory and call its functions against the SHARED family state,
loaded fresh at every call. Adapters hold no consent copies:
revocation written through the core is visible to every adapter
at its very next check (spec 5.3 — synchronous at check time).
That shared-state seam is the V0 fixture form of the live
registry service; it is stated here and in both READMEs.

Pure functions over a state dict: load_state()/save_state() are
the caller's job. Stdlib only.
"""
import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


class ToolError(Exception):
    pass


CHANNELS = {"voice", "sms", "voice+sms"}
SCOPES = {"transactional": 1, "conversational": 2, "marketing": 3}
SOURCES = {"web_form", "signed_doc", "recorded_call", "in_person",
           "platform_import", "other"}
REVOCATION_SOURCES = {"opt_out_keyword", "voice_request", "dnc_match",
                      "owner_action", "carrier_signal"}
PROVENANCE_KEYS = {"url", "document_ref", "call_ref", "form_id"}
ACTIVITY_KINDS = {"call", "sms", "voicemail"}
ACTIVITY_DISPOSITIONS = {"answered", "missed", "voicemail", "failed",
                         "blocked_consent", "blocked_window"}
NUMBER_CAPABILITIES = {"voice_in", "voice_out", "sms_in", "sms_out",
                       "mms", "fax"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_time(value):
    if value is None:
        return None
    text = str(value).strip()
    if len(text) == 10:
        text += "T00:00:00+00:00"
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def money(value) -> float:
    return round(float(value), 2)


# ---------------------------------------------------------------- receipts
def emit_receipt(state: dict, rtype: str, detail: dict) -> dict:
    """Append-only, hash-chained (house pattern)."""
    prev = state["receipts"][-1]["hash"] if state["receipts"] else "GENESIS"
    entry = {"seq": len(state["receipts"]) + 1, "ts": now_iso(),
             "type": rtype, "prev_hash": prev, "detail": detail}
    entry["hash"] = sha(prev + canon(
        {k: v for k, v in entry.items() if k != "hash"}))
    state["receipts"].append(entry)
    return entry


def emit_violation(state: dict, kind: str, detail: dict) -> dict:
    """A violation record is the ONLY ledger entry an unconsented
    action may produce (spec 8.3). It never carries a
    consent_record_id: nothing about it authorizes anything."""
    body = {"kind": kind}
    body.update(detail)
    return emit_receipt(state, "violation", body)


def verify_receipt_chain(receipts) -> bool:
    prev = "GENESIS"
    for entry in receipts:
        expect = sha(prev + canon(
            {k: v for k, v in entry.items() if k != "hash"}))
        if entry.get("hash") != expect or entry.get("prev_hash") != prev:
            return False
        prev = entry["hash"]
    return True


# ------------------------------------------------------------------ consent
def has_provenance(record: dict) -> bool:
    prov = record.get("provenance")
    return isinstance(prov, dict) and bool(
        PROVENANCE_KEYS & {k for k, v in prov.items() if v})


def consent_record_write(state: dict, args: dict) -> dict:
    """T1. New records only. A record without provenance is VOID
    (spec 5.2): the write is refused, nothing is stored, and the
    attempt is receipted as a violation."""
    subject = str(args.get("subject") or "").strip()
    channel = args.get("channel")
    scope = args.get("scope")
    if not subject:
        raise ToolError("subject is required")
    if channel not in CHANNELS:
        raise ToolError(f"channel must be one of {sorted(CHANNELS)}")
    if scope not in SCOPES:
        raise ToolError(f"scope must be one of {sorted(SCOPES)}")
    if args.get("source") not in SOURCES:
        raise ToolError(f"source must be one of {sorted(SOURCES)}")
    if not args.get("purpose"):
        raise ToolError("purpose is required (verbatim capture context)")
    if not args.get("captured_at") or parse_time(args["captured_at"]) is None:
        raise ToolError("captured_at (ISO timestamp) is required")
    record = {
        "subject": subject, "channel": channel, "scope": scope,
        "purpose": args["purpose"], "source": args["source"],
        "captured_at": args["captured_at"],
        "provenance": args.get("provenance"),
        "method_detail": args.get("method_detail"),
        "jurisdiction_hint": args.get("jurisdiction_hint"),
        "status": "active", "revoked_at": None,
        "revocation_source": None,
        "history": [{"event": "recorded", "at": now_iso(),
                     "by": args.get("recorded_by", "operator")}],
    }
    if not has_provenance(record):
        emit_violation(state, "consent_without_provenance", {
            "subject": subject, "channel": channel, "scope": scope,
            "source": args["source"],
            "reason": "provenance is REQUIRED (spec 5.2); a consent "
                      "record without provenance is void and was "
                      "not stored"})
        raise ToolError(
            "PROVENANCE_REQUIRED: a consent record without provenance "
            "is void (spec 5.2); the write was refused and receipted "
            "as a violation")
    record["consent_id"] = "consent_" + sha(canon({
        k: record[k] for k in
        ("subject", "channel", "scope", "captured_at", "source")
        }))[:12]
    if record["consent_id"] in state["consent_records"]:
        raise ToolError(f"consent record already exists: "
                        f"{record['consent_id']}")
    state["consent_records"][record["consent_id"]] = record
    receipt = emit_receipt(state, "consent_recorded", {
        "consent_id": record["consent_id"], "subject": subject,
        "channel": channel, "scope": scope})
    return {"consent": record, "receipt_seq": receipt["seq"]}


def _channel_covers(record_channel: str, channel: str) -> bool:
    return record_channel == "voice+sms" or record_channel == channel


def _governing_record(state: dict, subject: str, channel: str,
                      scope: str):
    """Narrowest wins (spec 5.1): among channel-covering records,
    the exact-scope record governs; failing that, the narrowest
    broader-scope record. Within a scope class, the newest capture
    governs (a fresh re-consent supersedes a revocation; a fresh
    revocation supersedes an older consent). Void records (no
    provenance) are invisible here — they are no consent at all."""
    live = [r for r in state["consent_records"].values()
            if r["subject"] == subject
            and _channel_covers(r["channel"], channel)
            and has_provenance(r)]
    if not live:
        return None
    want = SCOPES[scope]
    for rank in range(want, 4):
        tier = [r for r in live if SCOPES[r["scope"]] == rank]
        if tier:
            return max(tier, key=lambda r: (r["captured_at"],
                                            r["consent_id"]))
    return None


def dnc_match(state: dict, subject: str):
    """Returns the list record that matched, or None."""
    for lst in state["dnc_lists"]:
        if subject in lst.get("entries", []):
            return lst
    return None


def _list_fresh(state: dict, lst: dict, at: datetime) -> bool:
    loaded = parse_time(lst["loaded_at"])
    stale_days = state["config"].get("dnc_stale_days", 30)
    return (at - loaded).days <= stale_days


def window_check(state: dict, jurisdiction, channel: str, scope: str,
                 at_time=None) -> dict:
    """Calling windows as data (spec 5.4). The window uses the
    SUBJECT's timezone; an unknown timezone for a marketing or
    conversational contact is outside the window = DENY."""
    at = parse_time(at_time) or datetime.now(timezone.utc)
    rules = {r["jurisdiction"]: r for r in state["windows"]}
    rule = rules.get(jurisdiction) if jurisdiction else None
    if rule is None:
        rule = rules.get("*")
    if rule is None or not rule.get("timezone"):
        return {"inside": False, "reason": "unknown_timezone",
                "jurisdiction": jurisdiction,
                "rule_basis": rule.get("basis") if rule else None,
                "local_time": None}
    local = at.astimezone(ZoneInfo(rule["timezone"]))
    start = rule["window_start"]
    end = rule["window_end"]
    hhmm = local.strftime("%H:%M")
    inside = start <= hhmm < end
    return {"inside": inside,
            "reason": "inside_window" if inside else "outside_window",
            "jurisdiction": jurisdiction or "*",
            "timezone": rule["timezone"], "local_time": local.isoformat(),
            "window": f"{start}-{end}", "rule_basis": rule["basis"]}


def consent_check(state: dict, subject: str, channel: str, scope: str,
                  at_time=None) -> dict:
    """T0. The single door every adapter uses (spec 7). Returns the
    verdict plus the DNC and window sub-verdicts that ride send
    receipts (spec 8.3). Writes nothing."""
    at = parse_time(at_time) or datetime.now(timezone.utc)
    out = {"subject": subject, "channel": channel, "scope": scope,
           "decision": "DENY", "consent_record_id": None}
    record = _governing_record(state, subject, channel, scope)
    if record is None:
        out["reason"] = "no_consent"
        out["detail"] = ("no provenance-bearing consent record covers "
                         "this subject/channel/scope")
        return out
    out["consent_record_id"] = record["consent_id"]
    if record["status"] != "active":
        out["reason"] = f"consent_{record['status']}"
        return out
    # DNC overlay (spec 5.4). The owner's internal list binds every
    # scope; national/state lists bind marketing + conversational.
    match = dnc_match(state, subject)
    if match:
        out["dnc_check"] = {"matched": True, "list_id": match["list_id"],
                            "list_name": match["name"],
                            "version": match["version"]}
        binds = match["kind"] == "internal" or scope in (
            "marketing", "conversational")
        if binds:
            out["reason"] = "dnc_match"
            return out
    else:
        out["dnc_check"] = {"matched": False}
    # Freshness: stale DNC data degrades marketing checks to DENY.
    if scope == "marketing":
        stale = [lst["list_id"] for lst in state["dnc_lists"]
                 if not _list_fresh(state, lst, at)]
        if stale:
            out["reason"] = "dnc_data_stale"
            out["stale_lists"] = stale
            return out
    # Calling window (voice + sms, marketing + conversational).
    if scope in ("marketing", "conversational"):
        wc = window_check(state, record.get("jurisdiction_hint"),
                          channel, scope, at_time)
        out["window_check"] = wc
        if not wc["inside"]:
            out["reason"] = wc["reason"]
            return out
    out["decision"] = "ALLOW"
    out["reason"] = "consent_active"
    out["consent_scope"] = record["scope"]
    return out


def consent_revoke(state: dict, args: dict) -> dict:
    """T1. One call, total effect (spec 5.3). Revocation is one-way:
    re-consent requires a NEW record with new provenance."""
    source = args.get("revocation_source")
    if source not in REVOCATION_SOURCES:
        raise ToolError(
            f"revocation_source must be one of {sorted(REVOCATION_SOURCES)}")
    consent_id = args.get("consent_id")
    subject = args.get("subject")
    targets = []
    if consent_id:
        rec = state["consent_records"].get(consent_id)
        if rec is None:
            raise ToolError(f"unknown consent record: {consent_id}")
        targets = [rec]
    elif subject:
        targets = [r for r in state["consent_records"].values()
                   if r["subject"] == subject and r["status"] == "active"]
        if not targets:
            raise ToolError(f"no active consent records for: {subject}")
    else:
        raise ToolError("consent_id or subject is required")
    revoked = []
    for rec in targets:
        if rec["status"] == "revoked":
            continue
        rec["status"] = "revoked"
        rec["revoked_at"] = now_iso()
        rec["revocation_source"] = source
        rec["history"].append({"event": "revoked", "at": rec["revoked_at"],
                               "revocation_source": source})
        revoked.append(rec["consent_id"])
    receipt = emit_receipt(state, "consent_revoked", {
        "consent_ids": revoked, "revocation_source": source,
        "subject": subject or targets[0]["subject"]})
    return {"revoked": revoked, "receipt_seq": receipt["seq"]}


def consent_history(state: dict, subject: str) -> dict:
    records = [r for r in state["consent_records"].values()
               if r["subject"] == subject]
    records.sort(key=lambda r: r["captured_at"])
    return {"subject": subject, "records": records,
            "count": len(records)}


def dnc_status(state: dict, subject=None, at_time=None) -> dict:
    at = parse_time(at_time) or datetime.now(timezone.utc)
    lists = [{"list_id": lst["list_id"], "name": lst["name"],
              "kind": lst["kind"], "version": lst["version"],
              "loaded_at": lst["loaded_at"],
              "provenance": lst.get("provenance"),
              "entries": len(lst.get("entries", [])),
              "fresh": _list_fresh(state, lst, at)}
             for lst in state["dnc_lists"]]
    out = {"lists": lists}
    if subject:
        match = dnc_match(state, subject)
        out["subject"] = subject
        out["matched_list"] = match["list_id"] if match else None
    return out


# ------------------------------------------------------------------ numbers
def number_sendable(rec: dict, channel: str, scope: str) -> dict:
    """Registration state decides sendability (spec 5.6): a number
    whose registration state is broken is NOT sendable for the
    affected scope, with the reason named."""
    reasons = []
    if rec["status"] != "active":
        reasons.append(f"number_status_{rec['status']}")
    cap = {"voice": "voice_out", "sms": "sms_out"}[channel]
    if cap not in rec["capabilities"]:
        reasons.append(f"capability_missing_{cap}")
    reg = rec["registration"]
    if channel == "sms":
        a2p = reg["a2p_10dlc"]
        tfv = reg["toll_free_verification"]
        local_ok = (a2p["campaign_state"] == "registered"
                    and a2p["brand_state"] == "registered"
                    and a2p["linked"])
        if a2p["campaign_state"] not in ("registered", "not_applicable"):
            reasons.append(
                f"a2p_campaign_{a2p['campaign_state']}")
        if not local_ok and tfv != "verified":
            if tfv == "unverified":
                if scope == "marketing":
                    reasons.append("toll_free_unverified_marketing")
                # transactional from an unverified TFN is sendable
                # at reduced trust (spec 5.6); noted, not blocked.
            elif not local_ok:
                reasons.append("sms_registration_incomplete")
    return {"sendable": not reasons, "reasons": reasons}


def number_public(rec: dict) -> dict:
    out = dict(rec)
    matrix = {}
    for channel in ("voice", "sms"):
        for scope in SCOPES:
            matrix[f"{channel}/{scope}"] = number_sendable(
                rec, channel, scope)
    out["sendable_matrix"] = matrix
    return out


def number_enroll(state: dict, args: dict) -> dict:
    """Operator enrollment (V0 fixture form). A number not in the
    registry does not exist to any adapter (spec 6). Live
    provisioning is an adapter T2 surface at V1, not this tool."""
    e164 = str(args.get("e164") or "").strip()
    if not e164.startswith("+") or not e164[1:].isdigit():
        raise ToolError("e164 must be an E.164 number (+digits)")
    caps = args.get("capabilities") or []
    bad = [c for c in caps if c not in NUMBER_CAPABILITIES]
    if bad:
        raise ToolError(f"unknown capabilities: {bad}")
    for rec in state["numbers"].values():
        if rec["e164"] == e164:
            raise ToolError(f"number already enrolled: {e164}")
    number_id = args.get("number_id") or "num_" + sha(e164)[:12]
    if number_id in state["numbers"]:
        raise ToolError(f"number_id already exists: {number_id}")
    reg = args.get("registration") or {}
    rec = {
        "number_id": number_id, "e164": e164,
        "platform": args.get("platform", "twilio"),
        "account_ref": args.get("account_ref"),
        "capabilities": caps,
        "owner_purpose": args.get("owner_purpose", "support"),
        "registration": {
            "a2p_10dlc": reg.get("a2p_10dlc", {
                "brand_state": "unregistered",
                "campaign_state": "unregistered", "linked": False}),
            "toll_free_verification": reg.get(
                "toll_free_verification", "not_applicable"),
            "stir_shaken": reg.get("stir_shaken"),
            "branded_id": reg.get("branded_id", "not_enrolled")},
        "health": {"spam_labels": args.get("spam_labels", [])},
        "status": args.get("status", "active"),
    }
    state["numbers"][number_id] = rec
    receipt = emit_receipt(state, "number_enrolled", {
        "number_id": number_id, "e164": e164})
    return {"number": number_public(rec), "receipt_seq": receipt["seq"]}


def find_number(state: dict, ref: str):
    if ref in state["numbers"]:
        return state["numbers"][ref]
    for rec in state["numbers"].values():
        if rec["e164"] == ref:
            return rec
    return None


def number_health_report(state: dict, number_id=None) -> dict:
    recs = ([state["numbers"][number_id]] if number_id
            else list(state["numbers"].values()))
    report = []
    for rec in recs:
        blocked = {k: v["reasons"] for k, v in
                   number_public(rec)["sendable_matrix"].items()
                   if not v["sendable"]}
        report.append({
            "number_id": rec["number_id"], "e164": rec["e164"],
            "status": rec["status"],
            "registration": rec["registration"],
            "spam_labels": rec["health"]["spam_labels"],
            "send_blocked": blocked})
    return {"numbers": report, "count": len(report)}


# --------------------------------------------------------------- send gate
def send_evaluate(state: dict, *, channel: str, from_ref: str,
                  subject: str, scope: str, platform: str,
                  mandate=None, estimated_cost=0.0, at_time=None,
                  confirm_token=None) -> dict:
    """The two-gate send evaluation (spec 8), in order:
    (1) Consent Registry, (2) Agent Wallet-shaped mandate. Either
    DENY stops the action. A send receipt carrying a
    consent_record_id is writable ONLY when consent ALLOWs; an
    unconsented attempt produces a violation record and nothing
    else (spec 8.3)."""
    at = parse_time(at_time) or datetime.now(timezone.utc)
    base = {"channel": channel, "subject": subject, "scope": scope,
            "platform": platform, "from_ref": from_ref}
    if state["config"].get("family_kill_switch"):
        receipt = emit_receipt(state, "send_evaluated", {
            **base, "decision": "DENY", "stage": "kill_switch",
            "reason": "family_kill_switch_engaged"})
        return {"decision": "DENY", "stage": "kill_switch",
                "reason": "family_kill_switch_engaged",
                "receipt_seq": receipt["seq"]}
    consent = consent_check(state, subject, channel, scope, at_time)
    if consent["decision"] != "ALLOW":
        violation = emit_violation(state, "send_without_consent", {
            **base, "consent_reason": consent["reason"],
            "related_consent_id": consent.get("consent_record_id"),
            "reason": "send attempted without live consent; refused "
                      "at the Consent Registry gate (spec 8)"})
        return {"decision": "DENY", "stage": "consent",
                "reason": consent["reason"], "consent": consent,
                "violation_receipt_seq": violation["seq"]}
    cid = consent["consent_record_id"]
    number = find_number(state, from_ref)
    if number is None:
        receipt = emit_receipt(state, "send_evaluated", {
            **base, "decision": "DENY", "stage": "number",
            "reason": "number_not_enrolled",
            "consent_record_id": cid})
        return {"decision": "DENY", "stage": "number",
                "reason": "number_not_enrolled",
                "consent_record_id": cid,
                "receipt_seq": receipt["seq"]}
    sendable = number_sendable(number, channel, scope)
    if not sendable["sendable"]:
        receipt = emit_receipt(state, "send_evaluated", {
            **base, "decision": "DENY", "stage": "number",
            "reason": "registration_broken",
            "sendable_reasons": sendable["reasons"],
            "consent_record_id": cid, "number_id": number["number_id"]})
        return {"decision": "DENY", "stage": "number",
                "reason": "registration_broken",
                "sendable_reasons": sendable["reasons"],
                "consent_record_id": cid,
                "receipt_seq": receipt["seq"]}
    # Gate 2 — the mandate (Wallet shape, evaluated by reference).
    def deny(reason, **extra):
        receipt = emit_receipt(state, "send_evaluated", {
            **base, "decision": "DENY", "stage": "mandate",
            "reason": reason, "consent_record_id": cid,
            "number_id": number["number_id"], **extra})
        return {"decision": "DENY", "stage": "mandate",
                "reason": reason, "consent_record_id": cid,
                "receipt_seq": receipt["seq"], **extra}
    if not mandate:
        return deny("mandate_required")
    expires = parse_time(mandate.get("expires_at"))
    if expires is None or expires <= at:
        return deny("mandate_expired",
                    mandate_id=mandate.get("mandate_id"))
    platforms = mandate.get("platforms")
    if platforms and platform not in platforms:
        return deny("platform_not_allowlisted",
                    mandate_id=mandate.get("mandate_id"))
    destinations = mandate.get("destinations")
    if destinations and subject not in destinations:
        return deny("destination_not_allowlisted",
                    mandate_id=mandate.get("mandate_id"))
    cost = money(estimated_cost or 0)
    if mandate.get("cap") is not None and cost > money(mandate["cap"]):
        return deny("over_cap", mandate_id=mandate.get("mandate_id"),
                    estimated_cost=cost)
    # The send_ref binds the confirm token to THIS send request. It
    # is derived from the request content only — never from ledger
    # position — so the ref quoted in an APPROVAL_REQUIRED verdict
    # is the ref the follow-up call recomputes.
    send_ref = "send_" + sha(canon({
        "channel": channel, "subject": subject, "scope": scope,
        "platform": platform, "number_id": number["number_id"],
        "mandate_id": mandate.get("mandate_id"),
        "estimated_cost": cost}))[:12]
    threshold = mandate.get("approval_threshold")
    if threshold is not None and cost > money(threshold):
        expected = "confirm-send-" + send_ref
        if confirm_token != expected:
            receipt = emit_receipt(state, "send_evaluated", {
                **base, "decision": "APPROVAL_REQUIRED",
                "stage": "mandate", "reason": "over_threshold",
                "consent_record_id": cid,
                "number_id": number["number_id"],
                "mandate_id": mandate.get("mandate_id"),
                "send_ref": send_ref, "estimated_cost": cost})
            return {"decision": "APPROVAL_REQUIRED",
                    "stage": "mandate", "reason": "over_threshold",
                    "consent_record_id": cid, "send_ref": send_ref,
                    "confirm_token_form": expected,
                    "receipt_seq": receipt["seq"]}
    receipt = emit_receipt(state, "send_authorized", {
        **base, "decision": "ALLOW",
        "consent_record_id": cid,
        "consent_scope": consent.get("consent_scope"),
        "window_check": consent.get("window_check"),
        "dnc_check": consent.get("dnc_check"),
        "number_id": number["number_id"],
        "mandate_id": mandate.get("mandate_id"),
        "estimated_cost": cost, "send_ref": send_ref})
    return {"decision": "ALLOW", "stage": "complete",
            "consent_record_id": cid, "consent": consent,
            "number_id": number["number_id"], "send_ref": send_ref,
            "receipt_seq": receipt["seq"]}


# ------------------------------------------------------------ activity emit
def activity_emit(state: dict, args: dict) -> dict:
    """T1. The emission door into the Customer Graph (spec 11.2).
    Adapters call this; nothing writes to the Graph directly.
    Blocked attempts emit too — blocked_consent is a first-class
    disposition."""
    kind = args.get("kind")
    if kind not in ACTIVITY_KINDS:
        raise ToolError(f"kind must be one of {sorted(ACTIVITY_KINDS)}")
    if args.get("direction") not in ("inbound", "outbound"):
        raise ToolError("direction must be inbound or outbound")
    disposition = args.get("disposition")
    if disposition not in ACTIVITY_DISPOSITIONS:
        raise ToolError(
            f"disposition must be one of {sorted(ACTIVITY_DISPOSITIONS)}")
    if not args.get("occurred_at") or not args.get("platform_ref"):
        raise ToolError("occurred_at and platform_ref are required")
    activity = {
        "activity_id": "act_" + sha(canon({
            "platform_ref": args["platform_ref"],
            "occurred_at": args["occurred_at"],
            "seq": len(state["activities"])}))[:12],
        "kind": kind, "direction": args["direction"],
        "occurred_at": args["occurred_at"],
        "parties": args.get("parties"),
        "platform_ref": args["platform_ref"],
        "duration_s": args.get("duration_s"),
        "disposition": disposition,
        "recording_ref": args.get("recording_ref"),
        "transcript_ref": args.get("transcript_ref"),
        "consent_ref": args.get("consent_ref"),
        "receipt_ref": args.get("receipt_ref"),
    }
    state["activities"].append(activity)
    receipt = emit_receipt(state, "activity_emitted", {
        "activity_id": activity["activity_id"], "kind": kind,
        "disposition": disposition})
    return {"activity": activity, "receipt_seq": receipt["seq"]}


def empty_state() -> dict:
    return {"consent_records": {}, "numbers": {}, "dnc_lists": [],
            "windows": [], "activities": [], "receipts": [],
            "config": {"family_kill_switch": False,
                       "dnc_stale_days": 30}}
