# Credential and sharing security

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
