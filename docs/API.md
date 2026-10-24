# API Reference — APAAR Nexus

This document details the REST API endpoints for APAAR Nexus.
All mutating requests require a valid CSRF token in the `X-CSRFToken` header.
All API responses follow the standard response envelope documented in `AGENTS.md`.

## Response Envelopes

### Success
```json
{
  "ok": true,
  "data": {},
  "meta": { "page": 1, "per_page": 25, "total": 100 }
}
```

### Error
```json
{
  "ok": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable error description",
    "details": {}
  }
}
```

---

## Health Check
- `GET /api/health` — Returns status, version, and synthetic data notice.

## Authentication & Session Management (Task A2)

### `POST /api/auth/login`
Authenticates a user and establishes a session. Rate limited to 5 failures per 60 seconds per IP+username.
- **Request Body:**
  ```json
  {
    "username": "VESIT",
    "password": "Demo@12345"
  }
  ```
- **Success Response (200):**
  ```json
  {
    "ok": true,
    "data": {
      "user": {
        "id": "uuid",
        "username": "VESIT",
        "full_name": "VESIT Administrator",
        "role": "INSTITUTION_ADMIN",
        "institution_id": "inst-uuid",
        "student_id": null,
        "is_active": true,
        "created_at": "2026-10-24T12:00:00"
      },
      "redirect_url": "/dashboard"
    }
  }
  ```
- **Error Responses:**
  - `401 UNAUTHENTICATED`: Invalid username or password.
  - `403 ACCOUNT_INACTIVE`: Account is deactivated.
  - `422 VALIDATION_ERROR`: Missing username or password.
  - `429 RATE_LIMITED`: Too many failed login attempts (lockout for 60 seconds).

### `POST /api/auth/logout` (and `POST /logout`)
Terminates the active session and logs an audit event.
- **Requires:** Authenticated user session + CSRF token.
- **Success Response (200):**
  ```json
  {
    "ok": true,
    "data": {
      "message": "Successfully logged out."
    }
  }
  ```

### `GET /api/auth/me`
Fetches current authenticated user profile attributes. Password hash is never returned.
- **Requires:** Authenticated user session.
- **Success Response (200):**
  ```json
  {
    "ok": true,
    "data": {
      "user": {
        "id": "uuid",
        "username": "VESIT",
        "role": "INSTITUTION_ADMIN",
        "institution_id": "inst-uuid"
      }
    }
  }
  ```
- **Error Response:** `401 UNAUTHENTICATED`

### HTML Routes
- `GET /login`: Official portal login page with centered card and inline/toast validation.
- `GET /`: Automatic role-based redirect (`AICTE_ADMIN`/`INSTITUTION_ADMIN` -> `/dashboard`, `STUDENT` -> `/passport`, `VERIFIER` -> `/verify-console`, `AUDITOR` -> `/reports`, unauthenticated -> `/login`).


## Member B: Development UI preview (B0)

- `GET /ui-kit` — HTML component gallery, registered only when the app factory starts with debug enabled and guarded by `app.debug` on every request. Otherwise returns the normal `404 NOT_FOUND` envelope. A production-configured app never registers it, even if debug is later switched on. No database or login is required for this explicitly development-only page.
- Optional `role`: `AICTE_ADMIN`, `INSTITUTION_ADMIN`, `STUDENT`, `VERIFIER`, `AUDITOR`, or `ANONYMOUS`. Invalid values return `422 VALIDATION_ERROR`.
- Optional `shell=public`: renders the public/login layout without a sidebar.
- Preview roles are template-local fixtures, not login users. No records, credentials, audit entries, or role assignments are created. All form, filter, pagination, tab, toast and dialog examples run in memory.
- `base.html` exposes `title`, `head`, `shell`, `page_title`, `content`, and `scripts` blocks. Extend `base_public.html` for login/public verification; request vendor scripts only through `scripts` using local `url_for('static', ...)` URLs.
- Role menus are centralized in `templates/_navigation.html`. A registered GET path becomes a link; unbuilt modules remain unavailable text. The logout control becomes a CSRF-protected POST form when Member A registers `auth.logout`; it stays disabled in the UI preview.

## Member B: Identity evidence service (B2)

Python interface (no HTTP endpoint until B4):

```python
from services.identity_resolution import scan

# Commit ingestion before invoking the service; scan owns its unit of work.
result = scan(institution_id=institution_id, submission_id=None, academic_year="2026-27")
payload = result.to_dict()  # JSON-ready, masked references only
```

`ScanResult` contains `scan_id`, `matches`, `edges`, `events`, `stats`, UTC
`started_at`/`finished_at`, and graph `nodes`. Events expose stable `event_key`,
`rule_key`, sorted `student_ids`, redacted `evidence`, `explanation`, and
`is_signal`. Shared-contact, shared-fee-payer and duplicate-upload events are
signals only; they never become identity matches. This service assigns no risk
score, changes no student status and creates no risk cases.

Fuzzy comparisons use the greater of RapidFuzz token-sort and WRatio, with
`system_policies.identity_name_threshold` (default 88, configurable through
`IDENTITY_NAME_THRESHOLD` as a fallback). Candidates share sorted-name initial,
academic year and canonical programme code, then at least one non-empty
corroborator. This avoids a global all-pairs comparison; dense corroborated
blocks can still be quadratic. Initial/year/programme blocking deliberately
trades recall for bounded candidate sets. Programme comparisons use programme
codes across institution-local UUIDs. Registration-pattern corroboration ignores
case and punctuation, retaining digits to avoid linking every sequential roll ID.

Institution administrators query only their own institution; other institution
IDs and anonymous/unsupported roles are rejected. AICTE administrators may scan
globally or focus on one institution while finding its cross-institution peers.
Submission focus selects anchor records, retaining matching peers for comparison.
An app-context system job without an HTTP request is trusted: an institution
scope is local, and no institution scope is global. Never expose trusted jobs
through an unauthenticated route. Future scan routes require authentication,
role authorization and CSRF for mutations.

Persistence is a projection per institution/submission/year/access scope. Stable
UUIDs and `evidence.identity_scope` upsert matches/edges and delete stale rows in
that scope only; repeated scans retain row IDs while updating `scan_id`. Consumers
must read one scope, rather than unioning all scope projections. Every graph edge
has an explanation. Contact/payer node IDs are keyed opaque values; labels never
contain raw references. The current audit service commits projection and
`IDENTITY_SCAN_FINISHED` together; failures roll back. Call only after committing
the caller's ingestion work, with no unrelated pending/flushed writes.

Integration notes for Member A:

- A6/authz are currently absent. The service imports A6 normalization, masking,
  format validation and `authz.scope_query` when available. Until then it uses
  documented Unicode/contact normalization, masked labels, the `apaar_regex`
  policy (12 digits by default), and explicit institution predicates. Adapter
  `validate_format` must return a boolean. All checks are synthetic format checks.
- A1 prevents duplicate institution/student-ID/year rows with a unique constraint.
  The pure `detect_duplicate_institute_id` detector supports incoming snapshots;
  ingestion must supply rejected conflicting rows if these are to enter scan output.
- A8 is absent. Duplicate submissions require explicit `SubmissionError.code ==
  DUPLICATE_SUBMISSION` or an audit entry whose `entity_id` is the submission UUID
  and whose action is `DUPLICATE_SUBMISSION`/`SUBMISSION_DUPLICATE`,
  `details.rule_key` is `DUPLICATE_SUBMISSION`, or `details.duplicate_submission`
  is true. Hash equality alone is not inferred as an ingestion flag.
- A3/A4 seed data is absent; tests use isolated synthetic fixtures without adding
  seed accounts or changing Member A's files. No model changes are required for
  sequential scope-idempotent scans. Concurrent scans of the same scope require
  caller serialization/retry; this service does not implement a distributed lock.

Manual verification: run `python -m pytest tests/test_identity.py -q`, inspect
`result.to_dict()` from a committed synthetic app-context dataset, rerun with the
same scope and compare match/edge IDs. Contact-only/payer-only pairs must produce
signals and zero identity matches. Graph click-testing becomes available in B4/B5.

## Member B: Explainable risk service (B3)

No HTTP routes are added in B3. Use the committed B2 result from a trusted job or
an authenticated administrator context:

```python
from services.identity_resolution import scan
from services.risk_engine import evaluate_all, explain, record_excess_intake_event, score_events

cases = evaluate_all(scan(academic_year="2026-27"))
for case in cases:
    paragraph = explain(case)
# After committing a REJECTED capacity admission event:
case = record_excess_intake_event(admission_event)
```

- `score_events(events, weights)` is pure and returns `(score, severity,
  breakdown)` using standard bands. Events may be B2 dataclasses or dictionaries.
  Points apply once per `event_key`, even across different rules. Conflicting
  rules for a key choose the highest configured weight with a lexical tie-break,
  independent of scan order. Unknown rules contribute zero points.
- `evaluate_all(scan_result)` reads `risk_weights`, merging partial overrides
  with AGENTS.md defaults. A missing policy logs a warning. Values must be
  non-negative integers. `risk_bands`, when present, is an object of inclusive
  `[minimum, maximum]` ranges, for example
  `{"LOW":[0,29],"MEDIUM":[30,59],"HIGH":[60,79],"CRITICAL":[80,100]}`.
  The four ordered ranges must cover every integer 0–100 without gaps/overlaps.
  Invalid policies fail before committing case changes.
- Signal-only totals have a safety ceiling of `min(29, LOW upper bound)`, even
  when configured weights are very large. Breakdown rows retain their raw points;
  `explain` identifies the cap. Other totals are capped at 100. Shared contacts,
  fee payers and duplicate uploads always retain signal status.
- Substantive events connect student components. Signals wholly inside one
  component attach there; other signals form separate cases labelled
  `Investigation signal`. Signals never bridge two identity components. An event
  is allocated once per evaluation, including when it touches several records.
- Evidence is cumulative. Repeated/partial scans cannot erase earlier evidence
  or reopen/resolve a human decision. Re-evaluation refreshes policy points while
  keeping case/reference, status, assignee, evidence IDs and history. If new
  substantive evidence joins cases, the oldest case survives with its workflow;
  every other case's action history is moved to it, redundant cases are removed,
  and evidence is deduplicated. The oldest case's status/assignee wins when merged
  workflows differ. Historical evidence is not automatically withdrawn after a
  source-record correction; this requires human review.
- Affected student/institution IDs and the stable cluster key are in
  `RiskEvidence.details._risk`; related IDs are also in `related_student_ids`.
  Cross-institution cases have `RiskCase.institution_id = null`. Case-list APIs
  must enforce authorized membership explicitly rather than exposing these
  cases to institution administrators. The current service permits only own
  local cases for institution administrators; AICTE may evaluate cross-institution
  cases. App-context jobs are trusted; anonymous/other request roles are rejected.
- `record_excess_intake_event` accepts the persisted ORM `AdmissionEvent` after
  its caller commits. It handles `result=REJECTED` with reason
  `CAPACITY_EXCEEDED` or `EXCESS_INTAKE_VIOLATION`; other results/reasons return
  `None`. Coordinate any additional reason code with Member A. Cases group by
  institution across programmes/years; evidence retains each attempt's programme,
  year, category and classification. Admission UUID is the exact event key.
  Repeated delivery adds no points; distinct rejected attempts each contribute.
- The service owns its transaction. Commit caller work first (including flushed
  writes). `audit_service.log` commits case/evidence/history and `RISK_EVALUATED`
  together; failures roll back. Same-cluster concurrent calls require caller
  serialization/retry. No student, credential or admission record is changed.
  The initial history note is `Case opened by system scan` (OPEN to OPEN); the
  existing action enum's `UNDER_REVIEW` value records this note without changing
  case status. A dedicated system-open action would require a Member A enum change.

Verification: `python -m pytest tests/test_risk.py -q` exercises scoring,
authorization, idempotency, cluster merging, policy changes and non-punitive
behavior. The five-scenario fixture creates LOW (malformed reference/shared
contact), MEDIUM (rejected excess intake), HIGH (40+35) and CRITICAL (40+35+15)
cases from real scan/admission engine output. This is not a seed verification:
`seed.py` is absent on main. Member A must provide those isolated scenarios in
A3/A4 before a real `seed + scan` check is possible. B4/B6 will provide browser
case pages; no risk buttons exist to click-test yet.
