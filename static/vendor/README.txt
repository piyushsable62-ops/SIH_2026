APAAR Nexus browser libraries (vendored for offline operation)

Chart.js 4.4.8, MIT
Local file: chart.umd.min.js
Pinned browser bundle: https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.js
The upstream UMD bundle is already minified; the local name is chart.umd.min.js.
License: Chart.js.LICENSE.md
SHA256 (LF-normalized): E4CF4D144B222634F2E64FF707CD57B953F8DE4B65B231CD5AC6BDE114648E4D
Official installation guidance: https://www.chartjs.org/docs/latest/getting-started/installation.html

Cytoscape.js 3.30.4, MIT
Local file: cytoscape.min.js
Pinned browser bundle: https://cdn.jsdelivr.net/npm/cytoscape@3.30.4/dist/cytoscape.min.js
License: Cytoscape.js.LICENSE
SHA256 (LF-normalized): 1BB5340E549511E111B31E5684872C949AD33D40EA5DBA0AD8E7D90C62C7B3B9
Official distribution: https://github.com/cytoscape/cytoscape.js

Downloaded with PowerShell Invoke-WebRequest on 2026-10-24:
Invoke-WebRequest -Uri 'https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.js' -OutFile 'static/vendor/chart.umd.min.js'
Invoke-WebRequest -Uri 'https://cdn.jsdelivr.net/npm/cytoscape@3.30.4/dist/cytoscape.min.js' -OutFile 'static/vendor/cytoscape.min.js'

Load only on pages that need them, in the base template's scripts block:
{% block scripts %}
<script src="{{ url_for('static', filename='vendor/chart.umd.min.js') }}" defer></script>
<script src="{{ url_for('static', filename='vendor/cytoscape.min.js') }}" defer></script>
{% endblock %}

Never add a runtime CDN fallback, package manager, or frontend build step.
For updates: download an explicit version, retain its license, update hashes here,
and test the UI kit while offline before committing.
