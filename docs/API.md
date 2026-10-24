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
