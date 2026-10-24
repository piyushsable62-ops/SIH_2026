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

## Member B: Development UI preview (B0)

- `GET /ui-kit` — HTML component gallery, registered only when the app factory starts with debug enabled and guarded by `app.debug` on every request. Otherwise returns the normal `404 NOT_FOUND` envelope. A production-configured app never registers it, even if debug is later switched on. No database or login is required for this explicitly development-only page.
- Optional `role`: `AICTE_ADMIN`, `INSTITUTION_ADMIN`, `STUDENT`, `VERIFIER`, `AUDITOR`, or `ANONYMOUS`. Invalid values return `422 VALIDATION_ERROR`.
- Optional `shell=public`: renders the public/login layout without a sidebar.
- Preview roles are template-local fixtures, not login users. No records, credentials, audit entries, or role assignments are created. All form, filter, pagination, tab, toast and dialog examples run in memory.
- `base.html` exposes `title`, `head`, `shell`, `page_title`, `content`, and `scripts` blocks. Extend `base_public.html` for login/public verification; request vendor scripts only through `scripts` using local `url_for('static', ...)` URLs.
- Role menus are centralized in `templates/_navigation.html`. A registered GET path becomes a link; unbuilt modules remain unavailable text. The logout control becomes a CSRF-protected POST form when Member A registers `auth.logout`; it stays disabled in the UI preview.
