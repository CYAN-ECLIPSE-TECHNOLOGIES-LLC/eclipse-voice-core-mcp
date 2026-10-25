"""Eclipse Voice Core v0.1 tests — consent law (spec 5), number
registry (5.6/6), the two-gate send evaluation (8/8.3), the
Graph emission door (11.2), and chain integrity."""
import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

import core_engine as eng  # noqa: E402
import server as srv  # noqa: E402

AT = "2026-10-25T17:00:00Z"  # 10:00 PDT — inside the CA window


def fresh_state():
    state = eng.empty_state()
    seed = json.loads((HERE / "fixtures.json").read_text())
    state["config"].update(seed["config"])
    state["dnc_lists"] = copy.deepcopy(seed["dnc_lists"])
    state["windows"] = seed["windows"]
    for rec in seed["numbers"]:
        state["numbers"][rec["number_id"]] = copy.deepcopy(rec)
    return state


def write_consent(state, subject="contact_ada", **over):
    args = {"subject": subject, "channel": "voice+sms",
            "scope": "marketing",
            "purpose": "Product updates, per the signup form",
            "source": "web_form", "captured_at": "2026-10-01T00:00:00Z",
            "provenance": {"form_id": "signup-2026-10",
                           "url": "https://example.invalid/signup"},
            "jurisdiction_hint": "US-CA"}
    args.update(over)
    return eng.consent_record_write(state, args)


MANDATE = {"mandate_id": "m-voice-1", "cap": 5.0,
           "platforms": ["twilio"], "approval_threshold": 1.0,
           "expires_at": "2026-12-31T00:00:00Z"}


def evaluate(state, **over):
    kw = {"channel": "sms", "from_ref": "num_cetech_main",
          "subject": "contact_ada", "scope": "marketing",
          "platform": "twilio", "mandate": dict(MANDATE),
          "estimated_cost": 0.05, "at_time": AT}
    kw.update(over)
    return eng.send_evaluate(state, **kw)


class ConsentLaw(unittest.TestCase):
    def test_write_without_provenance_is_void_and_violation(self):
        state = fresh_state()
        with self.assertRaises(eng.ToolError) as ctx:
            write_consent(state, provenance=None)
        self.assertIn("PROVENANCE_REQUIRED", str(ctx.exception))
        self.assertEqual(state["consent_records"], {})
        violations = [r for r in state["receipts"]
                      if r["type"] == "violation"]
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["detail"]["kind"],
                         "consent_without_provenance")
        # The record that was never stored authorizes nothing.
        out = eng.consent_check(state, "contact_ada", "sms",
                                "marketing", AT)
        self.assertEqual(out["decision"], "DENY")
        self.assertEqual(out["reason"], "no_consent")

    def test_injected_provenanceless_record_is_invisible(self):
        # Spec 5.2 acceptance: a fixture record missing provenance
        # fails every consent check, on every channel, at every
        # tier — even sitting directly in the registry state.
        state = fresh_state()
        state["consent_records"]["consent_fake"] = {
            "consent_id": "consent_fake", "subject": "contact_eve",
            "channel": "voice+sms", "scope": "marketing",
            "purpose": "claimed", "source": "other",
            "captured_at": "2026-10-01T00:00:00Z",
            "provenance": None, "method_detail": None,
            "jurisdiction_hint": "US-CA", "status": "active",
            "revoked_at": None, "revocation_source": None,
            "history": []}
        for channel in ("voice", "sms"):
            for scope in ("transactional", "conversational",
                          "marketing"):
                out = eng.consent_check(state, "contact_eve", channel,
                                        scope, AT)
                self.assertEqual(out["decision"], "DENY",
                                 (channel, scope))
                self.assertEqual(out["reason"], "no_consent")

    def test_valid_consent_allows_and_binds_scope(self):
        state = fresh_state()
        rec = write_consent(state)["consent"]
        out = eng.consent_check(state, "contact_ada", "sms",
                                "marketing", AT)
        self.assertEqual(out["decision"], "ALLOW")
        self.assertEqual(out["consent_record_id"], rec["consent_id"])
        # voice+sms record covers both channels.
        self.assertEqual(eng.consent_check(
            state, "contact_ada", "voice", "marketing",
            AT)["decision"], "ALLOW")

    def test_scope_never_widens(self):
        state = fresh_state()
        write_consent(state, scope="transactional")
        out = eng.consent_check(state, "contact_ada", "sms",
                                "marketing", AT)
        self.assertEqual(out["decision"], "DENY")
        self.assertEqual(out["reason"], "no_consent")
        # The narrow record does govern its own scope.
        self.assertEqual(eng.consent_check(
            state, "contact_ada", "sms", "transactional",
            AT)["decision"], "ALLOW")

    def test_narrowest_wins_after_reconsent(self):
        state = fresh_state()
        write_consent(state, scope="marketing",
                      captured_at="2026-10-01T00:00:00Z")
        broad = eng.consent_check(state, "contact_ada", "sms",
                                  "transactional", AT)
        self.assertEqual(broad["decision"], "ALLOW")
        # A later narrow transactional revocation governs
        # transactional sends only.
        narrow = write_consent(
            state, scope="transactional",
            captured_at="2026-10-10T00:00:00Z")["consent"]
        eng.consent_revoke(state, {
            "consent_id": narrow["consent_id"],
            "revocation_source": "owner_action"})
        out = eng.consent_check(state, "contact_ada", "sms",
                                "transactional", AT)
        self.assertEqual(out["decision"], "DENY")
        self.assertEqual(out["reason"], "consent_revoked")
        self.assertEqual(eng.consent_check(
            state, "contact_ada", "sms", "marketing",
            AT)["decision"], "ALLOW")

    def test_revoke_is_total_and_one_way(self):
        state = fresh_state()
        write_consent(state)
        eng.consent_revoke(state, {"subject": "contact_ada",
                                   "revocation_source": "opt_out_keyword"})
        out = eng.consent_check(state, "contact_ada", "sms",
                                "marketing", AT)
        self.assertEqual(out["decision"], "DENY")
        self.assertEqual(out["reason"], "consent_revoked")
        hist = eng.consent_history(state, "contact_ada")
        events = [e["event"] for r in hist["records"]
                  for e in r["history"]]
        self.assertEqual(events, ["recorded", "revoked"])
        # Re-consent is a NEW record with new provenance.
        write_consent(state, captured_at="2026-10-20T00:00:00Z",
                      provenance={"form_id": "re-signup-2026-10"})
        self.assertEqual(eng.consent_check(
            state, "contact_ada", "sms", "marketing",
            AT)["decision"], "ALLOW")


class DncAndWindows(unittest.TestCase):
    def test_national_dnc_blocks_marketing_not_transactional(self):
        state = fresh_state()
        write_consent(state, subject="+14155559999")
        out = eng.consent_check(state, "+14155559999", "sms",
                                "marketing", AT)
        self.assertEqual(out["decision"], "DENY")
        self.assertEqual(out["reason"], "dnc_match")
        self.assertEqual(out["dnc_check"]["list_id"],
                         "dnc_national_fixture")
        self.assertEqual(eng.consent_check(
            state, "+14155559999", "sms", "transactional",
            AT)["decision"], "ALLOW")

    def test_internal_suppression_blocks_every_scope(self):
        state = fresh_state()
        write_consent(state, subject="contact_blocked_fixture",
                      scope="transactional")
        out = eng.consent_check(state, "contact_blocked_fixture",
                                "sms", "transactional", AT)
        self.assertEqual(out["decision"], "DENY")
        self.assertEqual(out["reason"], "dnc_match")

    def test_stale_dnc_data_degrades_marketing_to_deny(self):
        state = fresh_state()
        for lst in state["dnc_lists"]:
            lst["loaded_at"] = "2026-06-01T00:00:00Z"  # > 30 days old
        write_consent(state)
        out = eng.consent_check(state, "contact_ada", "sms",
                                "marketing", AT)
        self.assertEqual(out["decision"], "DENY")
        self.assertEqual(out["reason"], "dnc_data_stale")
        status = eng.dnc_status(state, at_time=AT)
        self.assertTrue(all(not lst["fresh"]
                            for lst in status["lists"]))

    def test_window_uses_subject_timezone(self):
        state = fresh_state()
        write_consent(state)  # jurisdiction US-CA
        # 06:30Z = 23:30 PDT the night before: outside 8:00-21:00.
        out = eng.consent_check(state, "contact_ada", "voice",
                                "marketing", "2026-10-25T06:30:00Z")
        self.assertEqual(out["decision"], "DENY")
        self.assertEqual(out["reason"], "outside_window")
        self.assertEqual(out["window_check"]["timezone"],
                         "America/Los_Angeles")

    def test_unknown_timezone_denies_marketing(self):
        state = fresh_state()
        write_consent(state, subject="contact_no_tz",
                      jurisdiction_hint=None)
        out = eng.consent_check(state, "contact_no_tz", "sms",
                                "marketing", AT)
        self.assertEqual(out["decision"], "DENY")
        self.assertEqual(out["reason"], "unknown_timezone")


class NumberRegistry(unittest.TestCase):
    def test_unenrolled_number_does_not_exist(self):
        state = fresh_state()
        with self.assertRaises(eng.ToolError) as ctx:
            srv.t_number_get(state, {"number_ref": "+19995550100"})
        self.assertIn("NUMBER_NOT_ENROLLED", str(ctx.exception))
        self.assertIsNone(eng.find_number(state, "+19995550100"))

    def test_sendable_matrix_by_registration_state(self):
        state = fresh_state()
        healthy = eng.number_public(state["numbers"]["num_cetech_main"])
        self.assertTrue(
            healthy["sendable_matrix"]["sms/marketing"]["sendable"])
        broken = eng.number_public(
            state["numbers"]["num_campaign_broken"])
        sms = broken["sendable_matrix"]["sms/marketing"]
        self.assertFalse(sms["sendable"])
        self.assertIn("a2p_campaign_suspended", sms["reasons"])
        # Voice is not the broken scope on that number.
        self.assertTrue(
            broken["sendable_matrix"]["voice/marketing"]["sendable"])
        tf = eng.number_public(
            state["numbers"]["num_tollfree_unverified"])
        self.assertFalse(
            tf["sendable_matrix"]["sms/marketing"]["sendable"])
        self.assertTrue(
            tf["sendable_matrix"]["sms/transactional"]["sendable"])

    def test_health_report_names_spam_labels(self):
        state = fresh_state()
        report = eng.number_health_report(state)
        tf = [n for n in report["numbers"]
              if n["number_id"] == "num_tollfree_unverified"][0]
        self.assertEqual(tf["spam_labels"][0]["label"], "Spam Likely")


class SendGate(unittest.TestCase):
    def test_allow_receipt_carries_consent_proof(self):
        state = fresh_state()
        rec = write_consent(state)["consent"]
        out = evaluate(state)
        self.assertEqual(out["decision"], "ALLOW")
        receipt = state["receipts"][-1]
        self.assertEqual(receipt["type"], "send_authorized")
        self.assertEqual(receipt["detail"]["consent_record_id"],
                         rec["consent_id"])
        self.assertEqual(receipt["detail"]["consent_scope"],
                         "marketing")
        self.assertEqual(receipt["detail"]["number_id"],
                         "num_cetech_main")
        self.assertIn("window_check", receipt["detail"])
        self.assertIn("dnc_check", receipt["detail"])

    def test_revoked_consent_yields_violation_not_send_receipt(self):
        # Spec 8.3 task test: attempt with revoked consent ->
        # a violation record exists, and NO send receipt.
        state = fresh_state()
        write_consent(state)
        eng.consent_revoke(state, {"subject": "contact_ada",
                                   "revocation_source": "opt_out_keyword"})
        before = len(state["receipts"])
        out = evaluate(state)
        self.assertEqual(out["decision"], "DENY")
        self.assertEqual(out["stage"], "consent")
        new = state["receipts"][before:]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["type"], "violation")
        self.assertEqual(new[0]["detail"]["kind"],
                         "send_without_consent")
        self.assertNotIn("consent_record_id", new[0]["detail"])
        send_receipts = [r for r in state["receipts"]
                         if r["type"] in ("send_authorized",)]
        self.assertEqual(send_receipts, [])

    def test_mandate_denials(self):
        state = fresh_state()
        write_consent(state)
        self.assertEqual(evaluate(state, mandate=None)["reason"],
                         "mandate_required")
        expired = dict(MANDATE, expires_at="2026-01-01T00:00:00Z")
        self.assertEqual(evaluate(state, mandate=expired)["reason"],
                         "mandate_expired")
        wrong_platform = dict(MANDATE, platforms=["telnyx"])
        self.assertEqual(
            evaluate(state, mandate=wrong_platform)["reason"],
            "platform_not_allowlisted")
        wrong_dest = dict(MANDATE, destinations=["someone_else"])
        self.assertEqual(evaluate(state, mandate=wrong_dest)["reason"],
                         "destination_not_allowlisted")
        self.assertEqual(evaluate(state, estimated_cost=9.99)["reason"],
                         "over_cap")

    def test_threshold_requires_confirm_token_for_this_send(self):
        state = fresh_state()
        write_consent(state)
        out = evaluate(state, estimated_cost=2.50)
        self.assertEqual(out["decision"], "APPROVAL_REQUIRED")
        allowed = evaluate(state, estimated_cost=2.50,
                           confirm_token=out["confirm_token_form"])
        self.assertEqual(allowed["decision"], "ALLOW")

    def test_unenrolled_and_broken_from_numbers_deny(self):
        state = fresh_state()
        write_consent(state)
        out = evaluate(state, from_ref="+19995550100")
        self.assertEqual(out["reason"], "number_not_enrolled")
        out = evaluate(state, from_ref="num_campaign_broken")
        self.assertEqual(out["reason"], "registration_broken")
        self.assertIn("a2p_campaign_suspended",
                      out["sendable_reasons"])

    def test_kill_switch_freezes_sends(self):
        state = fresh_state()
        write_consent(state)
        state["config"]["family_kill_switch"] = True
        out = evaluate(state)
        self.assertEqual(out["decision"], "DENY")
        self.assertEqual(out["reason"], "family_kill_switch_engaged")


class ActivityEmit(unittest.TestCase):
    def test_blocked_attempt_emits(self):
        state = fresh_state()
        out = eng.activity_emit(state, {
            "kind": "sms", "direction": "outbound",
            "occurred_at": AT,
            "parties": {"from_number_id": "num_cetech_main",
                        "counterparty": "contact_ada"},
            "platform_ref": {"platform": "twilio",
                             "platform_object_id": "fixture-1"},
            "disposition": "blocked_consent"})
        self.assertEqual(out["activity"]["disposition"],
                         "blocked_consent")

    def test_bad_disposition_refused(self):
        state = fresh_state()
        with self.assertRaises(eng.ToolError):
            eng.activity_emit(state, {
                "kind": "sms", "direction": "outbound",
                "occurred_at": AT, "platform_ref": {},
                "disposition": "sent_anyway"})


class TransportAndChain(unittest.TestCase):
    def test_tools_list_is_exact_and_denylist_refused(self):
        names = {t["name"] for t in srv.tool_list()}
        self.assertEqual(names, set(srv.TOOLS))
        for forbidden in ("e911_config", "caller_id_spoof",
                          "consent_fabricate", "number_release"):
            resp = srv.handle({"jsonrpc": "2.0", "id": 1,
                               "method": "tools/call",
                               "params": {"name": forbidden,
                                          "arguments": {}}})
            self.assertEqual(resp["error"]["code"], -32602)

    def test_chain_verifies_and_detects_tamper(self):
        state = fresh_state()
        write_consent(state)
        evaluate(state)
        self.assertTrue(eng.verify_receipt_chain(state["receipts"]))
        state["receipts"][0]["detail"]["consent_id"] = "forged"
        self.assertFalse(eng.verify_receipt_chain(state["receipts"]))


if __name__ == "__main__":
    unittest.main()
