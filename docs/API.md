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


## Institutions, Programmes, Capacities & Policies (Task A5)

### `GET /api/institutions`
Lists institutions with query filtering and pagination.
- **Access:** `AICTE_ADMIN` and `AUDITOR` see all institutions; `INSTITUTION_ADMIN` sees only their assigned institution.
- **Query Parameters:** `q` (search code/name/city), `state`, `status` (`ACTIVE`/`INACTIVE`), `page` (default 1), `per_page` (default 25, max 100).
- **Success Response (200):**
  ```json
  {
    "ok": true,
    "data": [
      {
        "id": "inst-uuid",
        "code": "VESIT",
        "name": "VESIT Mumbai",
        "state": "Maharashtra",
        "city": "Mumbai",
        "status": "ACTIVE",
        "programmes_count": 2,
        "total_regular_capacity": 240,
        "total_supernumerary_capacity": 20
      }
    ],
    "meta": { "page": 1, "per_page": 25, "total": 3 }
  }
  ```

### `POST /api/institutions`
Creates a new participating institution.
- **Access:** `AICTE_ADMIN` only.
- **Request Body:**
  ```json
  {
    "code": "IITB",
    "name": "Indian Institute of Technology Bombay",
    "state": "Maharashtra",
    "city": "Mumbai",
    "status": "ACTIVE"
  }
  ```
- **Success Response (201):** Returns created institution object.

### `GET /api/institutions/<id>`
Retrieves single institution details, degree programmes, and intake summary.
- **Access:** `AICTE_ADMIN` / `AUDITOR` any; `INSTITUTION_ADMIN` own institution only (403 for other institutions).

### `PATCH /api/institutions/<id>`
Updates institution metadata.
- **Access:** `AICTE_ADMIN` can update all fields; `INSTITUTION_ADMIN` can only update location/contact fields (`city`, `state`) on their own institution (403 if modifying code/name/status or modifying another institution).

### `GET /api/institutions/<id>/programmes` and `POST /api/institutions/<id>/programmes`
Lists or adds academic degree programmes. `POST` requires `AICTE_ADMIN` and validates unique `(institution_id, code)`.

### `GET /api/institutions/<id>/capacities` and `POST /api/institutions/<id>/capacities`
Lists or creates annual intake capacity for a programme. `POST` requires `AICTE_ADMIN`, validates non-negative intakes, and enforces unique `(institution_id, programme_id, academic_year)`.

### `PATCH /api/institutions/<id>/capacities/<capacity_id>`
Modifies approved regular intake, supernumerary quota, or category rules.
- **Access:** `AICTE_ADMIN` only.
- **Capacity Guard:** Rejects with `409 CONFLICT` if `approved_regular_intake` is set lower than current active regular student admissions in that programme and academic year.

### `GET /api/policies` and `PATCH /api/policies/<key>`
Retrieves or updates system-wide governance configuration.
- **Access:** `AICTE_ADMIN` only.
- **Shape Validation:**
  - `capacity_warning_thresholds`: Strictly ascending list of integers &le; 100.
  - `risk_weights`: Dictionary mapping rule keys to non-negative integers.
  - `sharing_allowed_expiry_hours`: List of integers that must be a subset of `{24, 72, 168}`.
  - `apaar_regex`: Non-empty string that compiles as a valid regular expression.
- **Audit:** Every change is recorded in `AuditLog` with old and new values.

### HTML Routes
- `GET /institutions`: Searchable institution directory table and interactive programme/capacity drawer.
- `GET /policies`: System policy cards with inline validation (AICTE Admin only).



## Member B: Development UI preview (B0)

- `GET /ui-kit` — HTML component gallery, registered only when the app factory starts with debug enabled and guarded by `app.debug` on every request. Otherwise returns the normal `404 NOT_FOUND` envelope. A production-configured app never registers it, even if debug is later switched on. No database or login is required for this explicitly development-only page.
- Optional `role`: `AICTE_ADMIN`, `INSTITUTION_ADMIN`, `STUDENT`, `VERIFIER`, `AUDITOR`, or `ANONYMOUS`. Invalid values return `422 VALIDATION_ERROR`.
- Optional `shell=public`: renders the public/login layout without a sidebar.
- Preview roles are template-local fixtures, not login users. No records, credentials, audit entries, or role assignments are created. All form, filter, pagination, tab, toast and dialog examples run in memory.
- `base.html` exposes `title`, `head`, `shell`, `page_title`, `content`, and `scripts` blocks. Extend `base_public.html` for login/public verification; request vendor scripts only through `scripts` using local `url_for('static', ...)` URLs.
- Role menus are centralized in `templates/_navigation.html`. A registered GET path becomes a link; unbuilt modules remain unavailable text. The logout control becomes a CSRF-protected POST form when Member A registers `auth.logout`; it stays disabled in the UI preview.

## Member B: Identity evidence service (B2)

Python interface (HTTP endpoints are documented in B4 below):

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

- A6 and authz are available and used. The service imports A6 normalization, masking,
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
- A3 reference data and A4 student scenarios are available. Tests use isolated synthetic fixtures without adding
  seed accounts or changing Member A's files. No model changes are required for
  sequential scope-idempotent scans. Concurrent scans of the same scope require
  caller serialization/retry; this service does not implement a distributed lock.

Manual verification: run `python -m pytest tests/test_identity.py -q`, inspect
`result.to_dict()` from a committed synthetic app-context dataset, rerun with the
same scope and compare match/edge IDs. Contact-only/payer-only pairs must produce
signals and zero identity matches. Graph browser click-testing is documented in B5 below.

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
`seed.py` now supplies A3/A4 data. The B5 isolated seed + scan check produced 365
students, 14 identity events and 11 cases after recording seeded rejected capacity
attempts. Bands present were LOW, MEDIUM and CRITICAL; HIGH is missing. Member A
should add a separate pair with one valid shared synthetic APAAR, ACTIVE at two
institutions in the same year, matching programme code/admission date, different
names and no additional corroborating signals. This scores 40+35=75 (HIGH).
The existing simultaneous-enrolment pair has a programme conflict, adding 15 and
making it CRITICAL. B6 will provide the dedicated browser case pages.

## Member B: Investigation APIs and human workflow (B4)

All endpoints below require an authenticated session and use the standard
`{ok,data}` / `{ok:false,error:{code,message,details}}` envelope. All POST/PATCH
requests require `X-CSRFToken`. No endpoint changes student records or revokes
credentials. Every decision requires human review.

| Method and path | Roles | Behavior |
|---|---|---|
| `POST /api/identity/scan` | AICTE, institution admin | JSON object with optional `institution_id`, `academic_year`; institution users are forced to their own scope. Runs scan then risk evaluation in the same request. Returns `scan_id`, `stats`, `cases_created`, `cases_updated`, `case_count`. Both services audit their runs. |
| `GET /api/identity/graph` | AICTE, institution admin, auditor | Read-only stored graph with masked nodes, explained edges and `truncated`. Does not run a scan. |
| `GET /api/identity/node/<node_id>` | Same read roles | Typed node ID from graph; returns `node`, `linked_records` (masked), `institutions`, `edges`, `linked_cases`, `truncated`. Foreign/missing nodes return 404. |
| `GET /api/risk/cases` | Same read roles | Descending score, UUID tie-break; returns a list with pagination `meta`. |
| `GET /api/risk/cases/<id>` | Same read roles | Case summary, score `breakdown`, `evidence`, masked `affected_records`, `affected_institutions`, recommendation, assignee, `history`, `explanation`, `allowed_next_actions`. |
| `POST /api/risk/cases/<id>/actions` | AICTE, limited institution response | `{action,note?,assigned_to?}`; see state machine below. Case action and audit commit together. |
| `PATCH /api/risk/cases/<id>` | AICTE only | `{assigned_to}`; active AICTE administrator username or `null` to clear. Assignment and audit commit together. Does not change status. |

Graph filters: `institution_id`, `academic_year`, `case_id`, `apaar_masked`
(`XXXXXXXX` + four digits only), `min_severity` (`LOW/MEDIUM/HIGH/CRITICAL`),
`include_signals` (`true/false/1/0`, default true), `limit` (1–1000, default 300
nodes). Node panels accept the same filters and ceiling. Only edges whose two
endpoints are present are returned. Node shape is
`{id,type,label,institution,severity,meta}`; edge shape is
`{id,source,target,type,explanation}`. Shared-reference labels/opaque IDs never
contain contacts or fee-payer values. Visible sharing counts are recomputed within
the filtered view. Overlapping stored scan projections are deduplicated by fact;
corrected reference links are omitted until a fresh scan supplies current links.
The view is bounded; use institution/year/case filters for larger datasets.

Case-list filters: `severity`, `status`, `institution_id`, `assigned_to`, `q`,
`page` (default 1), `per_page` (default 25, max 100). An empty `assigned_to` selects
unassigned cases. `q` is a literal substring of reference or summary (max 200
characters). `meta` includes `page`, `per_page`, `total`, `pages`. AICTE/auditor
institution filters include national cases whose affected-institution metadata
contains that institution. Institution administrators still only see wholly local
cases with their own `institution_id`; foreign and national case details return
404. They cannot obtain foreign records through graph edges or node panels.

Action-to-status mapping:

| Action | Target status |
|---|---|
| `UNDER_REVIEW` | `UNDER_REVIEW` |
| `REQUEST_CLARIFICATION` | `CLARIFICATION_REQUESTED` |
| `CONFIRM_DATA_ERROR` | `DATA_ERROR_CONFIRMED` |
| `DISMISS_FALSE_POSITIVE` | `FALSE_POSITIVE` |
| `RESOLVE` | `RESOLVED` |

Allowed AICTE transitions:

| Current status | Allowed next statuses |
|---|---|
| `OPEN` | `UNDER_REVIEW`, `CLARIFICATION_REQUESTED`, `FALSE_POSITIVE` |
| `UNDER_REVIEW` | `CLARIFICATION_REQUESTED`, `DATA_ERROR_CONFIRMED`, `FALSE_POSITIVE`, `RESOLVED` |
| `CLARIFICATION_REQUESTED` | `UNDER_REVIEW`, `DATA_ERROR_CONFIRMED`, `FALSE_POSITIVE`, `RESOLVED` |
| `DATA_ERROR_CONFIRMED` | `RESOLVED` |
| `FALSE_POSITIVE` | `UNDER_REVIEW` (reopen), `RESOLVED` |
| `RESOLVED` | `UNDER_REVIEW` (reopen, AICTE only) |

Invalid transitions return 409 `CONFLICT`, with
`error.details.allowed_next_actions` containing action names. Unknown action
names, malformed JSON/fields and invalid reviewers return 422. Notes are required
for `DISMISS_FALSE_POSITIVE`, `CONFIRM_DATA_ERROR`, and `RESOLVE`; notes must be
text of at most 4000 characters. Optional `assigned_to` on an action uses the same
reviewer validation as PATCH and is atomic with that transition.

An institution administrator may only send
`{action:"REQUEST_CLARIFICATION",note:"...response..."}` on a wholly local case
already in `CLARIFICATION_REQUESTED`. A non-empty note is required; the status
stays unchanged. History records the same from/to status and an institution
response note; audit action is `RISK_CASE_RESPONSE`. They cannot assign reviewers,
resolve or reopen cases. Auditors cannot mutate anything (403).

Transitions use an optimistic status/update-time guard; a concurrent stale update
returns 409 without appending history. Case status/optional assignment,
`case_actions` row, and `RISK_CASE_ACTION` audit row share one transaction through
the existing audit service. Failure rolls them all back. Assignment PATCH writes
`RISK_CASE_ASSIGNED`; it does not invent a status-transition history entry.
Scan/evaluation remain two separately audited commits in one request; if risk
evaluation fails, the committed identity projection remains available for retry.

PowerShell/manual checks after seeding and login (use the CSRF meta token from
`GET /login` and the same `WebRequestSession` for subsequent requests):

```powershell
$headers = @{ 'X-CSRFToken' = $csrf; Accept = 'application/json' }
Invoke-RestMethod "$base/api/identity/scan" -Method Post -WebSession $session -Headers $headers -ContentType 'application/json' -Body '{}'
Invoke-RestMethod "$base/api/risk/cases?per_page=25" -WebSession $session
Invoke-RestMethod "$base/api/identity/graph?include_signals=false" -WebSession $session
Invoke-RestMethod "$base/api/risk/cases/$caseId/actions" -Method Post -WebSession $session -Headers $headers -ContentType 'application/json' -Body '{"action":"UNDER_REVIEW","note":"Review started"}'
```

Verify repeated scans update cases, an invalid OPEN-to-RESOLVED transition returns
409, required notes return 422 when absent, and institution login hides foreign
cases/nodes. The graph screen is available in B5; the case screen is B6. These endpoints can be
exercised through PowerShell or the browser's developer console using `Nexus.api`.

## Member B: Identity graph explorer (B5)

- `GET /identity-graph` — HTML page for `AICTE_ADMIN`, `INSTITUTION_ADMIN`, and
  `AUDITOR`. Anonymous users go to login; unsupported roles receive 403.
- `GET /identity-graph?case_id=<case UUID>` — authorizes the case before rendering,
  requests only its cluster, highlights it and fits the view. Foreign case IDs
  remain indistinguishable from missing cases (404).
- The page uses local `static/vendor/cytoscape.min.js`, shared `Nexus` helpers and
  `static/js/identity-graph.js`. No CDN or extension layout is required. Native
  controls select cose/circle/breadthfirst, zoom, fit and reset the current layout.
  The 65/35 graph/Inspector layout stacks below 1050px.
- Institution options are rendered only for AICTE; year options are scoped on the
  server. Auditors have no scan button. Scan confirmation uses the native dialog
  and CSRF-protected `Nexus.api`; only the selected institution/year define scan
  scope. Severity, search and signal filters affect the view, not the scan scope.
- The graph API now also accepts `q` (normalized student-name substring, max 150
  characters). The single search field routes `XXXXXXXX1234` to `apaar_masked`
  and names to `q`. Full 12-digit input is rejected before making a request;
  the server rejects full identifiers in `q` as well. Name filtering happens before
  applying the node limit. B4 endpoint URLs and envelopes remain unchanged.
- Graph edges add a redacted `evidence` object alongside their existing fields.
  Internal scan-scope keys are omitted, and shared-reference counts reflect only
  visible records. Record summaries add `programme_name` for the Inspector table.
  Truncation prioritizes scored records and budgets relationship bundles so the
  default 300-node view retains entity/edge context instead of showing 300 isolated
  student nodes. Both endpoints of every returned edge remain in the response.
- Canvas selection and text-view Inspect buttons load the same node panel or
  explain an edge in readable key/value form. All visible nodes/edges have a text
  alternative. Keyboard users can tab to every native control and Inspect button;
  a focused canvas also accepts `+`, `-`, and `0` (fit). Loading text appears only
  while its request is active. Stale responses cannot replace a newer selection.
- Related case anchors use `/risk-cases/<id>`. Until B6 registers that page,
  ordinary clicks show an authorized read-only API-backed case preview in the
  Inspector with a Back to node button. Once the route exists, the links navigate
  normally. Direct/new-tab case URLs still require the B6 page.

Manual browser checklist (performed against an isolated in-memory A3/A4 seed,
with exactly the four official logins and three institutions):

1. Sign in as `AICTE_Admin`, open `/identity-graph`, and verify a truncation warning
   on the default view. The final preview returned 300 nodes and 308 relationships.
2. Search `XXXXXXXX0010` and turn signals off: two Devansh Verma student nodes,
   two institutions, one masked APAAR node and seven explained relationships.
3. Click a canvas student or its text Inspect button. Verify COEP/VESIT programme
   names, academic year, ACTIVE status, masked reference and related cases. Select
   simultaneous enrolment and verify its full explanation/evidence fields.
4. Switch all three layouts; use zoom, Fit and Reset view. Turn signals on and
   verify the additional contact/fee-payer shapes and dashed relationships.
5. Confirm Run identity scan; verify the success toast and refreshed graph. Search
   a nonexistent name and verify “No records match the filters”.
6. Open a returned case's graph deep link and verify the cluster-only banner and
   highlight. Test a related-case preview and Back to node.
7. Check institutional scope, hidden institution selector, auditor read-only
   controls and unauthorized deep links (also covered in pytest). The small
   browser viewport stacks panels; the desktop viewport shows two columns.

Seed follow-up: HIGH severity is missing from A4's current dataset; the exact
scenario to add is documented in B3 above. No Member A files were changed.
