# Credential and sharing security

## Scope and trust model

APAAR Nexus is a **local, synthetic-data prototype**. It runs on Flask's
development server and SQLite for demonstration. Institution administrators
are trusted to submit their own institutional data; AICTE administrators may
review across institutions. Public visitors can verify a signed credential or
open a student-issued sharing link. Neither public route can edit a student or
credential. A signature proves that this prototype's development issuer signed
the payload; it does not prove a government registry approved the person or
degree.

## Authentication, authorisation and sessions

Flask-Login holds the authenticated user in a server-signed session cookie.
Passwords are checked against Werkzeug password hashes; plain passwords are
never stored in the user table. The development seed creates exactly four
logins: `AICTE_Admin`, `VESIT`, `COEP`, and `VJTI`. No student, verifier or
auditor login is seeded. Login failures are rate-limited. The session cookie is
`HttpOnly` and `SameSite=Lax`; `Secure` is enabled in production configuration
and can be configured for HTTPS development. Set a unique `SECRET_KEY` and
`DEMO_PASSWORD` outside source control for any nonlocal deployment.

Every private API must enforce login and a role check on the server. The five
supported roles are `AICTE_ADMIN`, `INSTITUTION_ADMIN`, `STUDENT`, `VERIFIER`,
and `AUDITOR`. Institution-scoped queries use `services/authz.py` so an
institution admin cannot inspect a peer institution; AICTE admins may see the
national view. Auditor access is read-only. UI menu hiding is convenience, not
an authorisation boundary. Tests cover cross-institution denial and read-only
roles. The `/api/students?reveal=true` administrative exception can return a
full external identifier to an authorised admin and is audit-logged; default
views and exports mask it.

## Request, upload and browser protections

Flask-WTF CSRF protection applies to POST/PATCH/DELETE requests. Private pages
put the CSRF value in a meta tag and `static/js/app.js` sends `X-CSRFToken` for
JSON mutations. Public verification is GET-only. Jinja autoescaping remains on;
renderers put untrusted values in text nodes. CSV import accepts only `.csv`,
uses the configured 2 MB default size limit and 5,000-row default limit,
validates UTF-8 and headers, and previews row errors before commit. Downloaded
CSVs neutralise spreadsheet-formula prefixes. Production needs malware
scanning, hardened reverse-proxy limits, shared rate limiting and penetration
testing.

## Privacy and audit defaults

APAAR is an external reference, not a database primary key or a global unique
constraint. Public, report, risk, graph and passport views show only the masked
reference (`XXXXXXXX1234` style). Name similarity requires corroborating
evidence; shared contact or fee-payer links are investigation signals, never
proof of duplicate identity. Risk scores do not alter student records, find
fraud, or revoke credentials automatically. Human case actions, scan runs,
issuance, revocation, sharing, verification and exports create audit entries.
The append-only audit interface retains actor, action, time and scope; the
prototype has no tamper-proof external log store or retention policy. Keep
request paths containing bearer sharing tokens out of production access logs.

## What is outside this prototype

There are **no zero-knowledge proofs, blockchain, decentralised identity,
live APAAR/UIDAI/DigiLocker/AICTE connections, production HSM/KMS, external
revocation service or trusted time-stamping**. APAAR validation checks the
prototype format rule and a labelled mock adapter only. The Flask server,
SQLite database, local development keys and per-process public rate limiter
are unsuitable for a national deployment. Production would require authorised
registry contracts, formal identity assurance, a managed database and WSGI
deployment, HSM/KMS signing, verified public-key distribution, key recovery,
shared rate limits, monitoring, disaster recovery, privacy review and security
audit.

This prototype uses **Ed25519 signatures with development keys**. It does not
implement zero-knowledge proofs, blockchain or decentralised identity. All data
is synthetic; there is no AICTE, APAAR, UIDAI or DigiLocker integration.

## Keys and signature verification (B7)

Member A's `services/key_store.py` owns key loading. Development private PEM
files live in `SIGNING_KEY_DIR` (default `instance/keys/`). Never commit these
files, `.env`, database files or passwords. `signing_keys` contains public PEM
metadata only. Issuance checks that the private key matches the registered active
Ed25519 public key before signing. Verification never loads a private key or
generates a key: it uses the public key recorded under the credential's `key_id`.

The signed JSON has a strict allowlist. It contains a synthetic institutional
student reference and masked APAAR, not full APAAR, DOB, address or contacts.
Canonicalization is sorted compact JSON, Unicode UTF-8. Verification checks the
signature over the exact stored bytes, checks canonical encoding/schema and
compares signed claims with database columns before considering revocation and
expiry. Formatting edits invalidate the signature too. Renaming a referenced
institution or changing a student's external academic reference can invalidate
the live binding and requires a human correction/reissue policy in production.

Risk does not automatically deny issuance or revoke anything. Open cases require
explicit human acknowledgement. Only the four official, active seeded issuer
accounts with the appropriate administrative role can issue/revoke in this demo.
Requests cannot supply another user's identity. Unrelated pending/flushed writes
must be committed before service calls; the existing audit service owns the final
commit. Rollback tests cover credential and audit failure. Sequential duplicate
issuance returns CONFLICT; deployment must serialize same-student issuance or add
a Member A database uniqueness/idempotency contract for concurrent retries.

Rotation: register a new public key, provision its corresponding private key,
switch `ACTIVE_KEY_ID`, and mark the old signing key inactive for new issuance.
Retain old public keys for historical verification. The per-credential key ID
selects the correct public key independently of the active signing key. Missing
private files currently cause Member A's development key store to generate a
replacement; issuance refuses it if its public key differs. Production loading
must fail closed rather than regenerate a missing signing key.

Production requires HSM/KMS-backed signing, a controlled key ceremony, least
privilege/rotation, encrypted audited backups and tested recovery, authenticated
public-key distribution, a revocation list or OCSP-style status endpoint, trusted
time-stamping and an agreed historical-claims policy. The demo has none of that
production trust infrastructure. Its backend uses the server UTC clock and a
database revocation flag, not an external trust service.

Verification records store only a keyed hash of the remote IP and internal event
metadata. Invalid outcomes expose no untrusted claims; public revocation exposes
only a generic reason category, never the issuer's internal note. Public GETs may
append verification/audit events but never change credential or student state.

## Public verification (B9)

Public verdicts come only from backend checks, never a hard-coded UI badge.
Unknown references intentionally render INVALID with HTTP 200, just like other
credential states. HTML and JSON share a 30/minute hashed-IP limiter, bounded to
10,000 recent address buckets per application process. A multi-worker production
deployment needs a shared limiter and trusted proxy/address configuration.
No forwarded IP header is trusted by this demo. Results are no-store. Public
templates suppress the signed-in account header, unrelated flashes and CSRF
metadata. Demo tampering is disabled when SHOW_DEMO_ACCOUNTS is false.
# Consent and sharing links

Sharing uses 32 random bytes (`secrets.token_urlsafe(32)`), a SHA-256 database
lookup hash, and a six-character display prefix. The create response discloses
the bearer token/link once; list responses never recover it. Consent rows store
the selected claim list. Personal name and masked APAAR each require their own
explicit authorisation flag. Full APAAR, DOB, address and contact data have no
sharing claim and cannot be selected. Public responses contain only selected
claims; even student internal identifiers are omitted.

Every active access checks the stored consent, ownership, expiry and current
credential signature/revocation/expiry. Dead links disclose no claims. An
injectable server clock supports expiry tests. Unknown and malformed links use
the same generic 404 response. Responses disable caching, referrers and indexing;
the process limiter is shared across token paths. Access counts, pseudonymous
verification events and audit records do not include bearer tokens.

The sharing blueprint installs Werkzeug access-log redaction for bearer paths.
Production ingress, reverse proxies, error monitoring and analytics must also
redact `/public/verify/<token>`; never capture full request URLs on these routes.
The prototype has no third-party page resources on verification pages. A bearer
recipient can copy disclosed information, so revocation prevents future access
but cannot retract a copy already made. Database administrators are trusted;
hashes protect token recovery, not against an administrator changing consent.
