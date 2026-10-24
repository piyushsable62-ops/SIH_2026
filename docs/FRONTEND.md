# Shared frontend helpers — Member B handoff (B1)

Every page extending `base.html` or `base_public.html` loads `static/js/app.js` before its deferred page scripts. Use only `window.Nexus`; no package manager, build step or shared globals are needed. Add a page script in `{% block scripts %}` with `defer`. Keep Chart.js/Cytoscape.js opt-in and local.

Member A: after the checkpoint merge, sync your long-lived branch with `origin/main` and use these helpers in your pages. The browser controls presentation only: Flask must still enforce roles, institution scope, CSRF, validation and audit logging. The UI-kit role selector creates no accounts and changes no login session.

## 1. Read an API envelope

`api` returns the **whole envelope**, including pagination metadata. Empty query values (`null`, `undefined`, blank strings) are omitted; `0` and `false` are preserved; arrays become repeated parameters. Only same-origin URLs are accepted. No toast is shown automatically. HTTP 401 redirects to `/login?next=...` and throws `ApiError`. Network and malformed-response failures also throw `ApiError`; do not print response bodies or secrets.

```javascript
try {
  const { data, meta } = await Nexus.api('/api/students', {
    params: { page: 1, per_page: 25, institution_id: selectedInstitution || '' }
  });
  // Render data using the endpoint's documented shape; meta is on list endpoints.
} catch (error) {
  if (error instanceof Nexus.ApiError && error.status !== 401) {
    Nexus.toast(error.message, 'danger');
  }
}
```

## 2. JSON, file uploads and downloads

All non-GET requests read `csrf-token` from the page and send `X-CSRFToken`; a missing token fails locally with `CSRF_MISSING`. JSON gets its content type automatically. FormData is passed through without setting a content type, so the browser supplies the multipart boundary. GET/HEAD bodies are rejected. Successful 204/205 responses return `{ok:true, data:{}}`. `raw:true` returns the successful `Response` for the caller to consume; HTTP errors are still checked, and JSON `ok:false` still throws.

```javascript
await Nexus.api('/api/risk/cases/CASE_ID/actions', {
  method: 'POST', body: { action: 'UNDER_REVIEW' }
});
const upload = new FormData(document.querySelector('#upload-form'));
await Nexus.api('/api/students/import', { method: 'POST', body: upload });
const response = await Nexus.api('/api/students/export', { raw: true });
const csv = await response.text(); // Pass to your page's download handling.
```

## 3. Tables and pagination

Pass a container element or CSS selector. Rendering replaces its children, preventing duplicate tables or controls. Cell values and string render results are **plain text**, even if they contain markup. Only return a DOM node when custom markup is intended. `align` accepts `left`, `center`, or `right`. Empty tables show `emptyText`. Pagination clamps invalid/out-of-range pages and represents an empty set as page 1 of 1; callbacks receive the next page number.

```javascript
function renderPage(rows, meta) {
  Nexus.renderTable('#student-table', {
    columns: [
      { key: 'name', label: 'Student' },
      { key: 'risk_score', label: 'Risk score', align: 'right', render: Nexus.fmtNumber },
      { key: 'validation_status', label: 'Status', render: Nexus.statusBadge }
    ], rows, emptyText: 'No students match these filters.'
  });
  Nexus.paginate('#student-pages', meta, page => loadPage(page));
}
```

## 4. Filters and optional module options

Text fields debounce for 250ms. Select/date changes and Apply filters notify immediately. Clear resets fields and notifies once. `getValues()` returns strings; `setValues(values)` replaces the values silently (omitted fields become blank) and cancels pending text callbacks. `destroy()` removes the bar and cancels pending callbacks; rendering a replacement in the same container does this automatically.

`loadFilters.institutions(params)` and `.programmes(params)` return `{value,label}` arrays. The server-published `nexus-api-routes` meta tag prevents requests to missing modules. Missing modules, API failures or malformed option envelopes return `[]` with a warning toast (401 follows login handling). Institution loading defaults to `per_page:100`; supply `page` for further pages. Programme loading uses `/api/seats/summary` and accepts `institution_id`.

Supported payloads: `data` array; or `data.institutions` / `data.programmes` / `data.items` / `data.rows` / `data.summary` arrays. Institution rows use `id,name`; programme rows use `programme_id,programme_name`, nested `programme:{id,name}`, or `id,name`. Align the final API shape with these contracts; loaders never bypass server scoping.

```javascript
const institutions = await Nexus.loadFilters.institutions();
const filters = Nexus.filterBar('#filters', [
  { name: 'query', label: 'Search', type: 'text' },
  { name: 'institution_id', label: 'Institution', type: 'select', options: institutions },
  { name: 'admission_date', label: 'Admission date', type: 'date' }
], values => loadPage(1, values));
filters.setValues({ institution_id: 'INSTITUTION_UUID' });
const values = filters.getValues();
```

## 5. Toasts, confirmation and double-submit protection

Toast types: `info`, `ok`, `warn`, `danger` (unknown types fall back to info). Notifications stack in an `aria-live="polite"` container, are dismissible, and expire after 4000ms unless `ms=0`. Hover or keyboard focus pauses the timeout. Only the dismissal fades (140ms); reduced-motion users get immediate removal. Server flashes are enhanced with the same dismissal behaviour.

`confirmDialog` lazily creates one native dialog. Cancel/Escape resolve false; confirmation resolves true; an overlapping request resolves false. `withButtonLock` returns the callback result, suppresses repeated calls while pending, restores disabled/aria-busy state in `finally`, and lets failures propagate. It does not show spinners or notifications.

```javascript
Nexus.toast('Synthetic preview loaded.', 'info');
Nexus.toast('Review this message before continuing.', 'warn', 0);
revokeButton.addEventListener('click', () => {
  void Nexus.withButtonLock(revokeButton, async () => {
    if (!await Nexus.confirmDialog({ title: 'Revoke credential?',
      message: 'This action requires an authorized human decision.',
      confirmText: 'Revoke', danger: true })) return;
    await Nexus.api(`/api/credentials/${credentialId}/revoke`, { method: 'POST', body: {} });
    Nexus.toast('Credential revoked.', 'ok');
  }).catch(error => { if (error.status !== 401) Nexus.toast(error.message, 'danger'); });
});
```

## 6. Existing form dialogs

Define accessible native `<dialog>` markup in the page, with a labelled heading and form fields. IDs below must exist. Reopening an already open dialog and closing an already closed one are safe; a missing/non-dialog ID throws a TypeError. `closeDialog` uses return value `cancel`.

```javascript
document.querySelector('#open-filters').addEventListener('click', () => {
  Nexus.openDialog('advanced-filters-dialog');
});
document.querySelector('#close-filters').addEventListener('click', () => {
  Nexus.closeDialog('advanced-filters-dialog');
});
```

## 7. Formatting and badges

Formatting uses `en-IN` and the browser's timezone. Date-only ISO strings retain their calendar date; invalid/missing values display `—`. Percent inputs use the 0–100 scale. Contract statuses and case actions map to badges; unknown enums use muted. Badge text replaces underscores with spaces; data/API enums remain unchanged.

```javascript
Nexus.fmtDate('2026-10-24');                 // 24 Oct 2026
Nexus.fmtDateTime('2026-10-24T08:30:00Z');   // Local date and time
Nexus.fmtNumber(123456);                     // 1,23,456
Nexus.fmtPercent(80);                        // 80%
statusContainer.replaceChildren(Nexus.statusBadge('UNDER_REVIEW'));
statusContainer.append(Nexus.badge('Synthetic example', 'muted'));
```

## 8. Safe DOM construction

Use text/Node children, `class`/`className`, plain attributes, `dataset`, and `on:{event:listener}`. Boolean attributes are supported. The builder rejects executable tags, inline handlers, HTML injection attributes, inline styles, and unsafe URL schemes. `escapeHTML` is for plain text in an HTML-string context; it is not URL sanitization. Never use it as a reason to insert user-provided HTML.

```javascript
const label = '<b>Example & synthetic only</b>';
const button = Nexus.el('button', {
  type: 'button', class: 'btn btn-secondary',
  on: { click: () => Nexus.toast(label, 'info') }
}, label); // Displays literal angle brackets, never a <b> element.
actions.replaceChildren(button);
const escaped = Nexus.escapeHTML(label); // &lt;b&gt;Example &amp; synthetic only&lt;/b&gt;
```

## Manual verification checklist

Start with `.\.venv\Scripts\python.exe -m flask --app app:create_app run --debug --no-reload`, then open `/ui-kit`. All table/filter/dialog examples are synthetic and local. Do not use real student data or sharing tokens in browser tests.

- [ ] Toasts: exercise all four types; dismiss by keyboard; check stacking, 4-second timeout, hover/focus pause, sticky `ms=0`, and reduced-motion dismissal.
- [ ] Confirmation: confirm, Cancel and Escape; repeat several times; only one dialog should exist. Check focus returns to the invoking button. For an existing form dialog, call open twice and close twice.
- [ ] Table: Next/Previous, one page, empty result; type a no-match filter then Clear. No duplicated controls. Select a status; type quickly and confirm text changes debounce. Check `setValues` is silent and preserves `0` as text; date fields have labels.
- [ ] Safe rendering: pass `<img src=x onerror=alert(1)>` as a cell, badge and toast value; it must display as text. Custom render callbacks returning Nodes must still work. Invalid dates/NaN/null should show `—`; zero should display correctly.
- [ ] API success: Check API health returns healthy without a toast. Load institution options before the module exists: warning toast, `[]`, no request to the missing endpoint. Once available, verify authorized options and programme deduplication.
- [ ] API requests (DevTools with synthetic fixtures): check empty params omitted, `0/false` retained, existing query strings preserved, JSON content type, FormData boundary, non-GET CSRF header, raw response readability, 204 handling, network error and malformed JSON errors. Missing CSRF and cross-origin URLs must fail without sending a request.
- [ ] With a test-only 401 response, verify `/login?next=...`, then restore the UI kit. Check a 403/422 error retains `code`, `message`, `details`, `status`; the wrapper must not emit a toast on its own. Do not add public test endpoints to production.
- [ ] Rapidly invoke a locked button: one request only; button state restored on success, failure and cancellation. Review browser console, keyboard navigation, narrow layout and production `/ui-kit` exclusion.

Run the existing regression suite before a push: `.\.venv\Scripts\python.exe -m pytest -q`. These helpers add no database models, demo users, auth routes, or government integrations.

Local verification on 2026-10-24: 21 temporary browser-fixture checks passed (0 failed), including request headers, FormData, structured failures, raw/empty responses, 401 redirection, DOM safety, formatting, button locks, filters, dialogs, toasts and optional option loaders. UI-kit click-through also covered Escape cancellation, confirmation reuse, pagination/filtering and the actual health API. The fixture server was separate from the application; no fixture routes are shipped. Final integration with Member A's real auth/institution/seat APIs remains to be checked when those modules land. The checklist above is retained for that integration and accessibility review.
