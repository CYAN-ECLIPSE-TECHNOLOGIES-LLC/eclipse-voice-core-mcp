# Eclipse Voice Core MCP

**Powered by Cyan Eclipse Technologies** — https://cetech.ai

**Eclipse Voice Core — the Consent Registry and Number Registry every Eclipse telephony adapter obeys.**

**The Eclipse voice & telephony family — one Consent Registry and Number Registry govern every call and text across carriers, business phone systems, and AI voice platforms. No adapter dials or sends without a consent record with provenance and an Agent Wallet mandate; revocation propagates to every adapter instantly. It never touches emergency services, never spoofs a caller ID, never fabricates consent, and never lets an AI voice pretend to be a human.**

## Why this exists

Telephony is the highest-liability channel this organization will ever automate: every call and text is a regulated event, with statutory damages per violation, and liability sits with the business on whose behalf the contact is made — it cannot be shifted to a vendor. The carriers have shipped their pipes (official MCP servers included); none of them ships the layer above: one consent registry honored by *every* carrier at once, one mandate pattern, one receipt ledger, one kill switch. That layer is this core. CETech is not a carrier — the family governs other companies' rails.

## Status — honest labels

v0.1.0, phase **V0 (shared core, fixtures only)**. Local stdio transport, Python stdlib only. **No carrier account exists, no number is provisioned, no call is placed, no text is sent, and no certification is claimed — bench pending.** The first live phase (V1) sits behind two gates: the owner's own Twilio account and a counsel review of the DNC/window datasets and the recording-consent matrix (spec §16).

## The Consent Registry

One registry, honored by every adapter in the family. A consent record carries: subject, channel (`voice` / `sms` / `voice+sms`), scope (`transactional` / `conversational` / `marketing` — **narrowest wins; a record never silently widens**), the purpose verbatim from its capture context, source, capture time, jurisdiction hint, an append-only status history — and **provenance**.

**The provenance law (spec §5.2): a consent record without provenance is void.** Not flagged — void. The write path refuses it, stores nothing, and receipts the attempt as a violation; a provenance-less record injected directly into the registry state is invisible to every consent check, on every channel, at every scope (proven by test). Consent is never inferred from a relationship, a past purchase alone, or another platform's record.

**Revocation is instant, total, and one-way** (spec §5.3). Any signal — STOP keyword, voice request, DNC match, owner action, carrier feed — writes one registry event; adapters hold no consent copies and re-check live, so a contact revoked one second ago is uncallable and untextable on every platform one second later. Re-consent requires a *new* record with new provenance; there is no un-revoke edit, and the denylist says so in code.

**DNC overlays and calling windows are data, not code** (spec §5.4): versioned datasets with `{source, version, loaded_at, provenance}`; a check names *which* list matched. The owner's internal suppression list binds every scope; national/state lists bind marketing and conversational contact. Stale list data degrades marketing checks to DENY, never to ALLOW. Window checks run in the **subject's** timezone from jurisdiction rule data (federal baseline 08:00–21:00 called-party local); an unknown timezone is outside the window — DENY.

## The Number Registry

A number not in the registry **does not exist** to any adapter. Each enrolled number carries its platform, capabilities, owner purpose, health (spam-label observations — reported, never "fixed"), and its **registration state**: A2P 10DLC brand/campaign state and linkage, toll-free verification state, STIR/SHAKEN attestation *as reported by the carrier* (reported, never asserted by us), and branded-caller-ID enrollment. Registration state decides sendability per channel and scope: the shipped fixtures include a healthy 10DLC number, an unverified toll-free number (marketing SMS blocked; transactional sendable at reduced trust), and a number whose 10DLC campaign is suspended (SMS blocked with the reason named; voice unaffected — the registry blocks the *affected* scope, not the number).

## The send gate

Every T2+ send runs both gates **in this order: (1) Consent Registry, (2) Agent Wallet-shaped mandate** (cap, platform + destination allowlists, approval threshold, expiry), with Number Registry sendability between them. Either DENY stops the action; both verdicts ride the receipt.

**Consent proof travels with the act** (spec §8.3): an ALLOW receipt carries `consent_record_id`, `consent_scope`, the `window_check` and `dnc_check` results, `number_id`, and `mandate_id`. The ledger makes the alternative unwritable — an unconsented attempt produces a **violation record and nothing else**: no send receipt exists for it, ever. Over-threshold sends return `APPROVAL_REQUIRED` with a confirm token bound to that exact send reference.

Registry unreachable means T2/T3 actions DENY — the core is fail-closed, with one honest exception (spec §5.5): T0 reads of already-receipted history continue. A family-wide **kill switch** freezes every send evaluation at DENY while history stays readable.

## The adapter seam (V0 fixture form)

Adapters import `core_engine` from this directory and evaluate against the **shared family state file**, loaded fresh at every call — the V0 fixture form of the live registry service, stated here rather than hidden. `ECLIPSE_VOICE_CORE_STATE` points every process (core server, Trunk, any fixture adapter) at the same state file; that sharing is exactly what the V0 acceptance test exercises when one revocation refuses sends on two adapter surfaces at once.

## Tools (14)

| Tool | Tier | What it does |
|---|---|---|
| `consent_check` | T0 | The single consent door: ALLOW/DENY + reason + record id, with DNC and window sub-verdicts. |
| `consent_record_write` | T1 | New consent records only; provenance required — refused + violation-receipted without it. |
| `consent_revoke` | T1 | One call, total effect; one-way; receipted. |
| `consent_history` | T0 | A subject's full consent trail with status history. |
| `dnc_status` | T0 | Overlay datasets: versions, provenance, freshness; one subject's match state. |
| `window_check` | T0 | Window evaluation from jurisdiction rule data; the rule is cited. |
| `number_list` / `number_get` | T0 | Registry reads with sendability matrices; unenrolled refs refused as nonexistent. |
| `number_enroll` | T1 | Operator enrollment (V0 fixture form); live provisioning is an adapter T2 surface at V1. |
| `number_health_report` | T0 | Spam-label + registration-state report; what is blocked and why. |
| `send_evaluate` | T2 gate | Consent → number → mandate, in order; executes nothing; consent proof or violation record. |
| `activity_emit` | T1 | The emission door into the Customer Graph (spec §11.2); blocked attempts emit too. |
| `kill_switch_set` | T1 | Family-wide freeze of T2+ evaluations. |
| `receipt_list` | T0 | The append-only, hash-chained family ledger; verifies the chain. |

The denylist is stated in code: no emergency-services surface (`emergency_call`, `e911_config`, `test_911`…), no caller-ID surface (`caller_id_set`, `caller_id_spoof`…), no consent editing (`consent_edit`, `consent_unrevoke`, `consent_fabricate`), no `dnc_remove`, no `number_release` exists at any tier (spec §10).

## Run it

```bash
python3 server.py            # MCP over stdio, stdlib only, no dependencies
python3 smoke.py             # end-to-end smoke over real stdio (temp state)
python3 -m unittest discover tests
python3 make_icon.py         # regenerate icon-512.png
```

State persists in `data/state.json` beside the server (override with `ECLIPSE_VOICE_CORE_STATE`), seeded on first run from `fixtures.json` — small provenance-bearing fixture subsets (DNC lists, window rules, three numbers). `data/` is gitignored.

## Roadmap (per spec)

- **V0** — this core + the Eclipse Trunk (Twilio) fixture adapter.
- **V1** — first live: owner's Twilio account + one registered dogfood number; DNC/window datasets and the recording-consent matrix through counsel review.
- **V2** — Uplink (Telnyx) + Wavelength (Bandwidth) wraps; Stratum-2 phone-system adapters feeding the Customer Graph.
- **V3** — Eclipse Ward, the AI voice-agent governor. CETech places no AI outbound calls and enables no AI answering in v1 (spec decision 3).

## License / attribution

A Cyan Eclipse Technologies product. The "Powered by Cyan Eclipse Technologies" attribution travels with it.
