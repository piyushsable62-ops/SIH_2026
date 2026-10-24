# Security Review & Verification Report — Member A Modules
**Platform:** APAARSetu (formerly APAAR Nexus) — Smart India Hackathon PS 26263  
**Review Scope:** Member A Modules (Auth & RBAC, Institutions, Students, Admissions, Dynamic Seat Ledger, CSV Ingestion, Validation, Audit Service)  
**Reference Standard:** `AGENTS.md` Section 8 ("Security checklist")  
**Date:** October 2026  
**Status:** PASS (All checks verified with automated regression tests)

---

## Executive Summary

A comprehensive security audit and code hardening review was performed across all modules owned by Member A. All routes, database interactions, file handling pipelines, authentication flows, session handling mechanisms, and error reporting configurations were inspected against the security requirements specified in `AGENTS.md` §8.

Automated verification tests were added in [`tests/test_hardening_a.py`](file:///d:/SIH_2026/SIH_2026/tests/test_hardening_a.py) to permanently guarantee regression resistance.

---

## Detailed Check-by-Check Findings

### 1. Route Authorization & RBAC Enforcement
- **Requirement:** Every route must be protected by `@login_required` and role decorators (`@roles_required`), except public endpoints (`/api/auth/login`, `/api/health`, and the read-only public verification routes).
- **Audit Methodology:** 
  An automated test dynamically enumerates `app.url_map.iter_rules()` and verifies that every `/api` endpoint rejects unauthenticated requests with HTTP 401 or 302.
- **Finding:**
  During inspection, `GET /api/students/import/template` was initially missing `@login_required` and `@roles_required`.
- **Fix Applied:**
  Decorated `download_template()` in [`routes/students.py`](file:///d:/SIH_2026/SIH_2026/routes/students.py) with `@login_required` and `@roles_required(Role.AICTE_ADMIN.value, Role.INSTITUTION_ADMIN.value)`.
- **Verification Test:**
  `test_all_api_routes_require_authentication` in [`tests/test_hardening_a.py`](file:///d:/SIH_2026/SIH_2026/tests/test_hardening_a.py) passes.
- **Status:** **PASS**

---

### 2. Multi-Tenant Institution Isolation
- **Requirement:** Institution admins must never be able to read, update, reveal, or admit students into another institution. All scoped queries must route through `services.authz.scope_query`.
- **Audit Methodology:**
  Simulated requests from `COEP` admin attempting to access `VESIT` resources via both URL manipulation and API parameters.
- **Findings & Hardening:**
  - `GET /api/students/<id>`: Returns 403 `FORBIDDEN` if student belongs to a different institution.
  - `GET /students/<id>` (HTML): Returns 403 `FORBIDDEN`.
  - `PATCH /api/students/<id>`: Returns 403 `FORBIDDEN`.
  - `GET /api/students?institution_id=<foreign_id>`: Returns 403 `FORBIDDEN` if an institution admin requests a foreign institution ID.
  - `POST /api/admissions`: Returns 403 `FORBIDDEN` if `institution_id` differs from `current_user.institution_id`.
  - `POST /api/admissions/precheck`: Enforces `current_user.institution_id`.
  - `POST /api/students/import`: Enforces `current_user.institution_id`.
- **Verification Tests:**
  `test_cross_institution_isolation_students` and `test_cross_institution_isolation_admissions` in [`tests/test_hardening_a.py`](file:///d:/SIH_2026/SIH_2026/tests/test_hardening_a.py).
- **Status:** **PASS**

---

### 3. File Upload Limits, File Type & CSV Injection Protection
- **Requirement:** Input validated server-side. File uploads: `.csv` only, size limit from config (default 2 MB), UTF-8, max rows from config (5000), neutralization of spreadsheet formula injection characters.
- **Audit Methodology:**
  Tested file type validation, size checks, and dangerous formula prefixes (`=`, `+`, `-`, `@`, `\t`, `\r`).
- **Findings & Hardening:**
  - `MAX_CONTENT_LENGTH` is enforced at the Flask application level (2 MB).
  - Extension is strictly verified as `.csv`.
  - Encoding is validated as strict UTF-8 (surrogates and binary payloads rejected).
  - In `check_csv_injection()`, leading formula triggers (`=`, `+`, `-`, `@`, `\t`, `\r`) are sanitized by stripping the trigger before storage and previewing.
  - In `sanitize_csv_cell_for_export()`, cells beginning with formula triggers are escaped with a leading single quote (`'`) to prevent execution in Microsoft Excel or Google Sheets.
- **Verification Tests:**
  `test_csv_injection_cell_sanitization` in [`tests/test_hardening_a.py`](file:///d:/SIH_2026/SIH_2026/tests/test_hardening_a.py) and `tests/test_csv.py`.
- **Status:** **PASS**

---

### 4. CSRF Protection on Mutating Routes
- **Requirement:** CSRF token required on every mutating HTTP method (`POST`, `PATCH`, `DELETE`) from the browser.
- **Audit Methodology:**
  Inspected `extensions.py` (`CSRFProtect(app)`), base templates, and verified API wrapper in `app.js`.
- **Findings:**
  - `WTF_CSRF_ENABLED = True` in [`config.py`](file:///d:/SIH_2026/SIH_2026/config.py).
  - `<meta name="csrf-token" content="{{ csrf_token() }}">` is present in `templates/base.html`.
  - `window.Nexus.api` in `static/js/app.js` automatically attaches the `X-CSRFToken` header from the meta tag.
  - When a mutating call lacks a valid CSRF token in an environment with CSRF enabled, Flask-WTF halts execution with a 400 `CSRF_ERROR` JSON envelope.
- **Verification Tests:**
  `test_csrf_rejection_when_enabled` in `tests/test_auth.py`.
- **Status:** **PASS**

---

### 5. Session Cookie Security & Logout Invalidation
- **Requirement:** Passwords hashed with Werkzeug; session cookies set to `HttpOnly`, `SameSite=Lax`, and `Secure` when not in debug. Complete session invalidation upon logout.
- **Audit Methodology:**
  Inspected [`config.py`](file:///d:/SIH_2026/SIH_2026/config.py) and [`routes/auth.py`](file:///d:/SIH_2026/SIH_2026/routes/auth.py).
- **Findings & Hardening:**
  - `SESSION_COOKIE_HTTPONLY = True`
  - `SESSION_COOKIE_SAMESITE = "Lax"`
  - `SESSION_COOKIE_SECURE = True` in `ProductionConfig`.
  - In [`routes/auth.py`](file:///d:/SIH_2026/SIH_2026/routes/auth.py), `logout()` calls `logout_user()` and explicitly executes `session.clear()` to purge all session values, preventing session fixation or post-logout reuse.
- **Verification Tests:**
  `test_session_cookie_security_config` and `test_logout_invalidates_session` in [`tests/test_hardening_a.py`](file:///d:/SIH_2026/SIH_2026/tests/test_hardening_a.py).
- **Status:** **PASS**

---

### 6. Error Handling & Stack Trace Leakage Prevention
- **Requirement:** Error handlers must never leak tracebacks, internal file paths, or exception details to clients.
- **Audit Methodology:**
  Triggered an unhandled server exception with `DEBUG=False` and analyzed the HTTP response payload.
- **Findings:**
  - Global error handlers in [`app.py`](file:///d:/SIH_2026/SIH_2026/app.py) intercept unhandled exceptions and return:
    ```json
    {
      "ok": false,
      "error": {
        "code": "SERVER_ERROR",
        "message": "An internal server error occurred.",
        "details": {}
      }
    }
    ```
  - Traceback strings, exception type names, and file paths are logged to server logs (`app.logger.exception`) but are completely suppressed from the HTTP response.
- **Verification Test:**
  `test_unhandled_500_never_leaks_stack_trace` in [`tests/test_hardening_a.py`](file:///d:/SIH_2026/SIH_2026/tests/test_hardening_a.py).
- **Status:** **PASS**

---

### 7. Confidentiality & No Secrets in Logs
- **Requirement:** Passwords, private keys, raw tokens, and full 12-digit APAAR IDs must never appear in log files, audit rows, or unauthorized API responses.
- **Audit Methodology:**
  Inspected [`services/audit_service.py`](file:///d:/SIH_2026/SIH_2026/services/audit_service.py) data scrubbing logic and tested persisting sensitive dictionaries.
- **Findings:**
  - `_scrub_sensitive_data()` recursively detects keys containing `password`, `token`, `secret`, `private_key` and redacts them to `"[REDACTED]"`.
  - Full 12-digit numbers and keys containing `apaar` are masked via `services.apaar_adapter.mask_apaar` (e.g. `XXXXXXXX9012`).
  - Unmasked APAAR is only revealed upon deliberate, authorized request (`?reveal=true`) by AICTE admin or the student's own institution admin, and each reveal action writes a permanent audit log entry.
- **Verification Test:**
  `test_no_secrets_in_audit_logs` in [`tests/test_hardening_a.py`](file:///d:/SIH_2026/SIH_2026/tests/test_hardening_a.py).
- **Status:** **PASS**

---

### 8. SQL Injection & Parameter Binding
- **Requirement:** Use SQLAlchemy ORM only. No raw SQL string interpolation or format strings.
- **Audit Methodology:**
  Automated regex search across all `.py` files for string-interpolated SQL statements (`execute(f"...")`, `execute("%s")`, `execute("{}".format)`).
- **Findings:**
  - Zero raw SQL formatting found across the entire repository.
  - All database queries use SQLAlchemy ORM constructs (`select()`, `.filter()`, `.filter_by()`, `joinedload()`, or bound parameters).
- **Status:** **PASS**

---

### 9. Cross-Module Integration Hooks
- **Requirement:**
  1. CSV commit and manual student creation trigger `services.identity_resolution.scan(...)` and failures must never break the import (log + audit, continue).
  2. Capacity rejections call `risk_engine.record_excess_intake_event` and produce an excess intake risk case in `/api/risk/cases`.
  3. Student detail view links to risk cases and academic credentials created by Member B.
- **Findings & Hardening:**
  - In [`services/csv_service.py`](file:///d:/SIH_2026/SIH_2026/services/csv_service.py) and [`routes/students.py`](file:///d:/SIH_2026/SIH_2026/routes/students.py), `_trigger_identity_scan` is wrapped in exception handlers that log a warning, call `audit_service.log(..., action="IDENTITY_SCAN_ERROR")`, and allow the import/creation transaction to complete successfully.
  - In [`services/seat_ledger.py`](file:///d:/SIH_2026/SIH_2026/services/seat_ledger.py), rejection on `CAPACITY_EXCEEDED` calls `risk_engine.record_excess_intake_event(event)`, producing a `MEDIUM` severity risk case (30 points) with recommended action to review with the institution.
  - In [`routes/students.py`](file:///d:/SIH_2026/SIH_2026/routes/students.py) and [`templates/student_detail.html`](file:///d:/SIH_2026/SIH_2026/templates/student_detail.html), `linked_risk_cases` and `linked_credentials` are retrieved and rendered with direct links to `/risk-cases/<case_id>` and `/credentials/<cred_id>`.
- **Verification Tests:**
  `test_identity_scan_failure_never_breaks_student_creation`, `test_excess_intake_rejection_triggers_risk_case`, and `test_student_detail_links_risk_cases_and_credentials` in [`tests/test_hardening_a.py`](file:///d:/SIH_2026/SIH_2026/tests/test_hardening_a.py).
- **Status:** **PASS**

---

### 10. Performance & Query Sanity
- **Requirement:** Add indexes where list endpoints are slow with 300+ students; avoid N+1 queries in `/api/students`.
- **Findings & Hardening:**
  - Added composite indexes in [`models/student.py`](file:///d:/SIH_2026/SIH_2026/models/student.py):
    - `ix_student_inst_year_created` on `(institution_id, academic_year, created_at)`
    - `ix_student_inst_prog_year` on `(institution_id, programme_id, academic_year)`
    - Confirmed via SQLite `EXPLAIN QUERY PLAN` that sorting by `created_at desc` utilizes the composite index directly without requiring a temporary B-tree filesort.
  - In [`routes/students.py`](file:///d:/SIH_2026/SIH_2026/routes/students.py), added `.options(joinedload(StudentRecord.institution), joinedload(StudentRecord.programme))` to `list_students()` and `export_students_csv()`, ensuring institution and programme relations are loaded in a single query rather than $N+1$ queries.
- **Status:** **PASS**

---

## Conclusion
Member A's codebase complies fully with `AGENTS.md` §8. All identified gaps were remediated and verified with automated regression tests.
