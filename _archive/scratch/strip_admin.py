"""Strip admin.html to only user management + admin actions.
Remove: Agent Cost Monitor, Audit System Heartbeat, Audit-AI & Data Export, and their JS.
Keep: User roster, modals, role toggle, reset, add user JS.
"""
with open('templates/admin.html', 'r', encoding='utf-8') as f:
    content = f.read()

# ============================================================
# 1. Remove Agent Cost Monitor section (lines 144-200)
# ============================================================
old_cost = """    <!-- PHASE 1: AGENT COST INFRASTRUCTURE CARD -->
    <div class="roster-card" style="margin-top: 24px;">
        <div class="card-header">
            <span class="card-title">Agent Cost Monitor</span>
            <div style="display:flex; gap:10px; align-items:center;">
                <span id="budgetPctLabel" class="card-title" style="color:#64748b;">Loading...</span>
                <button class="btn-test" id="testBtn" onclick="runTestCall()">RUN TEST CALL</button>
            </div>
        </div>

        <!-- 24H STATS GRID -->
        <div class="cost-grid">
            <div class="cost-stat">
                <div class="cost-label">24H Spend</div>
                <div class="cost-value" id="todaySpend">\u2014</div>
                <div class="cost-sub" id="todayBudget">of $\u2014 daily cap</div>
            </div>
            <div class="cost-stat">
                <div class="cost-label">7-Day Spend</div>
                <div class="cost-value" id="weekSpend">\u2014</div>
                <div class="cost-sub" id="weekCalls">\u2014 total calls</div>
            </div>
        </div>

        <!-- BUDGET BAR -->
        <div class="budget-bar-wrap">
            <div class="budget-bar-bg">
                <div class="budget-bar-fill" id="budgetBar" style="width:0%"></div>
            </div>
        </div>

        <!-- LAST 10 CALLS TABLE -->
        <div style="padding: 0 0 4px;">
            <table class="cost-table">
                <thead>
                    <tr>
                        <th>Agent</th>
                        <th>Trigger</th>
                        <th>Status</th>
                        <th>In</th>
                        <th>Out</th>
                        <th>CR</th>
                        <th>Cost</th>
                        <th>Time</th>
                    </tr>
                </thead>
                <tbody id="costTableBody">
                    <tr><td colspan="8" style="color:#64748b; text-align:center; padding:20px;">Loading...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- TEST CALL OUTPUT -->
        <div style="padding: 0 20px 16px;">
            <div id="testResult"></div>
        </div>
    </div>

    <!-- AUDIT SYSTEM HEARTBEAT CARD -->
    <div class="roster-card" style="margin-top: 24px;">
        <div class="card-header">
            <span class="card-title">Audit System Heartbeat</span>
            <span id="hbRefreshLabel" class="card-title" style="color:#64748b; font-size:10px;">auto-refresh 60s</span>
        </div>
        <div style="padding: 16px 20px; display: flex; gap: 32px; flex-wrap: wrap;">
            <div>
                <div style="font-family:'JetBrains Mono'; font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">session_audit_log</div>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span id="hbAuditDot" style="width:10px; height:10px; border-radius:50%; background:#334155; display:inline-block;"></span>
                    <span id="hbAuditStatus" style="font-family:'JetBrains Mono'; font-size:13px; font-weight:800; color:#64748b;">\u2014</span>
                </div>
                <div id="hbAuditDetail" style="font-family:'JetBrains Mono'; font-size:10px; color:#64748b; margin-top:4px;">loading\u2026</div>
            </div>
            <div>
                <div style="font-family:'JetBrains Mono'; font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">monitor_event_log</div>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span id="hbMonDot" style="width:10px; height:10px; border-radius:50%; background:#334155; display:inline-block;"></span>
                    <span id="hbMonStatus" style="font-family:'JetBrains Mono'; font-size:13px; font-weight:800; color:#64748b;">\u2014</span>
                </div>
                <div id="hbMonDetail" style="font-family:'JetBrains Mono'; font-size:10px; color:#64748b; margin-top:4px;">loading\u2026</div>
            </div>
        </div>
    </div>

    <!-- AUDIT-AI + DATA EXPORT CARD -->
    <div class="roster-card" style="margin-top: 24px;">
        <div class="card-header">
            <span class="card-title">Audit-AI &amp; Data Export</span>
            {% if latest_daily_digest %}
            <span class="card-title" style="color:#64748b; font-size:10px;">last digest: {{ latest_daily_digest.date_key }}</span>
            {% endif %}
        </div>

        <div style="padding: 16px 20px 8px;">
            {% if latest_daily_digest %}
            <div style="font-family:'JetBrains Mono'; font-size:12px; color:#94a3b8; margin-bottom:16px;">
                Today's digest ({{ latest_daily_digest.date_key }}):
                <b style="color:#fff;">{{ latest_daily_digest.trades_covered_15m }}</b> 15M \u00b7
                <b style="color:#fff;">{{ latest_daily_digest.trades_covered_1h }}</b> 1H \u00b7
                <b style="color:#fff;">{{ latest_daily_digest.trades_covered_4h }}</b> 4H trades covered.
            </div>
            {% else %}
            <div style="font-family:'JetBrains Mono'; font-size:12px; color:#64748b; margin-bottom:16px;">No daily digest yet \u2014 first run fires at 23:45 UTC tonight.</div>
            {% endif %}

            {% if recent_suggestions %}
            <table style="width:100%; border-collapse:collapse; margin-bottom:16px;">
                <thead>
                    <tr>
                        <th style="text-align:left; font-family:'JetBrains Mono'; font-size:10px; color:#64748b; text-transform:uppercase; padding:6px 8px; border-bottom:1px solid #334155;">Hypothesis</th>
                        <th style="text-align:left; font-family:'JetBrains Mono'; font-size:10px; color:#64748b; text-transform:uppercase; padding:6px 8px; border-bottom:1px solid #334155;">Tier</th>
                        <th style="text-align:left; font-family:'JetBrains Mono'; font-size:10px; color:#64748b; text-transform:uppercase; padding:6px 8px; border-bottom:1px solid #334155;">N</th>
                        <th style="text-align:left; font-family:'JetBrains Mono'; font-size:10px; color:#64748b; text-transform:uppercase; padding:6px 8px; border-bottom:1px solid #334155;">Suggestion</th>
                    </tr>
                </thead>
                <tbody>
                    {% for s in recent_suggestions %}
                    <tr>
                        <td style="font-family:'JetBrains Mono'; font-size:11px; color:#fff; padding:6px 8px; border-bottom:1px solid #1e293b; white-space:nowrap;">{{ s.hypothesis_id }}</td>
                        <td style="font-family:'JetBrains Mono'; font-size:11px; color:#94a3b8; padding:6px 8px; border-bottom:1px solid #1e293b; white-space:nowrap;">{{ s.tier_label }}</td>
                        <td style="font-family:'JetBrains Mono'; font-size:11px; color:#94a3b8; padding:6px 8px; border-bottom:1px solid #1e293b;">{{ s.n_supporting }}</td>
                        <td style="font-family:'Inter'; font-size:11px; color:#cbd5e1; padding:6px 8px; border-bottom:1px solid #1e293b;">{{ s.suggestion_text }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <div style="font-family:'JetBrains Mono'; font-size:12px; color:#64748b; margin-bottom:16px;">No Audit-AI findings logged yet \u2014 H1-H6 fire weekly (Sunday 23:00 UTC), H7-H9 fire daily (23:45 UTC).</div>
            {% endif %}

            <div style="display:flex; align-items:flex-end; gap:16px; flex-wrap:wrap; padding-bottom:16px;">
                <div>
                    <label style="display:block; font-family:'JetBrains Mono'; font-size:10px; color:#64748b; text-transform:uppercase; margin-bottom:4px;">Start Date</label>
                    <input type="date" id="exportStartDate" style="background:#0f172a; border:1px solid #334155; color:#fff; padding:8px 10px; border-radius:4px; font-family:'JetBrains Mono'; font-size:12px;">
                </div>
                <div>
                    <label style="display:block; font-family:'JetBrains Mono'; font-size:10px; color:#64748b; text-transform:uppercase; margin-bottom:4px;">End Date</label>
                    <input type="date" id="exportEndDate" style="background:#0f172a; border:1px solid #334155; color:#fff; padding:8px 10px; border-radius:4px; font-family:'JetBrains Mono'; font-size:12px;">
                </div>
                <button class="btn-test" id="exportCopyBtn" onclick="copyAuditExport()">COPY JSON</button>
                <span id="exportStatus" style="font-family:'JetBrains Mono'; font-size:11px; color:#64748b;"></span>
            </div>
        </div>
    </div>"""

if old_cost not in content:
    print("ERROR: Could not find cost/export sections to remove!")
    # Try to find the start
    idx = content.find('AGENT COST INFRASTRUCTURE')
    print(f"Found 'AGENT COST INFRASTRUCTURE' at {idx}")
    idx2 = content.find('AUDIT-AI + DATA EXPORT')
    print(f"Found 'AUDIT-AI + DATA EXPORT' at {idx2}")
    idx3 = content.find('AUDIT SYSTEM HEARTBEAT')
    print(f"Found 'AUDIT SYSTEM HEARTBEAT' at {idx3}")
else:
    content = content.replace(old_cost, '', 1)
    print("1. Removed Agent Cost Monitor, Audit Heartbeat, and Audit-AI/Export sections")

# ============================================================
# 2. Remove heartbeat JS block (lines 319-374)
# ============================================================
old_hb_js = """    <script>
        async function loadAuditHeartbeat() {
            try {
                const res = await fetch('/api/health/audit-heartbeat');
                const d = await res.json();
                if (!d.ok) return;

                const sal = d.session_audit_log || {};
                const mel = d.monitor_event_log || {};

                const auditDot = document.getElementById('hbAuditDot');
                const auditSt  = document.getElementById('hbAuditStatus');
                const auditDt  = document.getElementById('hbAuditDetail');
                if (sal.status === 'WRITING') {
                    auditDot.style.background = '#10b981';
                    auditSt.style.color = '#10b981';
                    auditSt.textContent = 'WRITING';
                    auditDt.textContent = `last: ${sal.last_date_key || '\u2014'} (${sal.last_status || '\u2014'})  |  recent: ${sal.recent_count ?? '\u2014'} rows`;
                } else if (sal.status === 'TABLE_MISSING') {
                    auditDot.style.background = '#ef4444';
                    auditSt.style.color = '#ef4444';
                    auditSt.textContent = 'TABLE MISSING';
                    auditDt.textContent = sal.error || '';
                } else {
                    auditDot.style.background = '#f59e0b';
                    auditSt.style.color = '#f59e0b';
                    auditSt.textContent = 'DARK';
                    auditDt.textContent = 'Table exists \u2014 no rows yet';
                }

                const monDot = document.getElementById('hbMonDot');
                const monSt  = document.getElementById('hbMonStatus');
                const monDt  = document.getElementById('hbMonDetail');
                if (mel.status === 'WRITING') {
                    monDot.style.background = '#10b981';
                    monSt.style.color = '#10b981';
                    monSt.textContent = 'WRITING';
                    monDt.textContent = `last session: ${mel.last_session_date || '\u2014'}  poll seq: ${mel.last_poll_seq ?? '\u2014'}  |  recent: ${mel.recent_count ?? '\u2014'} rows`;
                } else if (mel.status === 'TABLE_MISSING') {
                    monDot.style.background = '#ef4444';
                    monSt.style.color = '#ef4444';
                    monSt.textContent = 'TABLE MISSING';
                    monDt.textContent = mel.error || '';
                } else {
                    monDot.style.background = '#f59e0b';
                    monSt.style.color = '#f59e0b';
                    monSt.textContent = 'DARK';
                    monDt.textContent = 'Table exists \u2014 no rows yet';
                }
            } catch (e) {
                console.warn('Heartbeat fetch failed:', e);
            }
        }
        loadAuditHeartbeat();
        setInterval(loadAuditHeartbeat, 60000);
    </script>"""

if old_hb_js not in content:
    print("ERROR: Could not find heartbeat JS!")
    idx = content.find('loadAuditHeartbeat')
    print(f"Found 'loadAuditHeartbeat' at {idx}")
else:
    content = content.replace(old_hb_js, '', 1)
    print("2. Removed heartbeat JS")

# ============================================================
# 3. Remove cost card JS (lines 517-599)
# ============================================================
old_cost_js = """    <script>
        // --- AGENT COST CARD ---
        async function loadCostData() {
            try {
                const res = await fetch('/api/agents/cost');
                const data = await res.json();
                if (!data.ok) return;

                const t = data.today;
                const w = data.seven_day;
                const pct = t.budget_pct || 0;

                document.getElementById('todaySpend').textContent = '$' + t.total_usd.toFixed(4);
                document.getElementById('todayBudget').textContent = 'of $' + t.budget_usd.toFixed(2) + ' daily cap';
                document.getElementById('weekSpend').textContent = '$' + w.total_usd.toFixed(4);

                const weekCalls = Object.values(w.by_agent || {}).reduce((s, a) => s + a.calls, 0);
                document.getElementById('weekCalls').textContent = weekCalls + ' total calls';

                document.getElementById('budgetPctLabel').textContent = pct.toFixed(1) + '% of daily budget';

                const bar = document.getElementById('budgetBar');
                bar.style.width = Math.min(pct, 100) + '%';
                bar.className = 'budget-bar-fill' + (pct >= 90 ? ' danger' : pct >= 70 ? ' warn' : '');

                const tbody = document.getElementById('costTableBody');
                const calls = data.last_10_calls || [];
                if (calls.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" style="color:#64748b; text-align:center; padding:20px;">No calls logged yet. Run a test call.</td></tr>';
                    return;
                }
                tbody.innerHTML = calls.map(r => {
                    const sc = r.status === 'SUCCESS' ? 'status-ok' : r.status === 'BUDGET_BLOCKED' ? 'status-blocked' : 'status-err';
                    const cr = r.cache_read_tokens > 0 ? '<span style="color:#10b981">' + r.cache_read_tokens + '</span>' : '0';
                    return `<tr>
                        <td>${r.agent_name}</td>
                        <td style="color:#64748b">${r.triggered_by}</td>
                        <td class="${sc}">${r.status}</td>
                        <td>${r.input_tokens}</td>
                        <td>${r.output_tokens}</td>
                        <td>${cr}</td>
                        <td style="color:#facc15">$${r.estimated_cost_usd.toFixed(6)}</td>
                        <td style="color:#64748b; font-size:10px">${(r.created_at || '').replace(' UTC','')}</td>
                    </tr>`;
                }).join('');

            } catch(e) {
                console.error('Cost load failed:', e);
            }
        }

        async function runTestCall() {
            const btn = document.getElementById('testBtn');
            const out = document.getElementById('testResult');
            btn.disabled = true;
            btn.textContent = 'FIRING...';
            out.style.display = 'none';

            try {
                const res = await fetch('/api/agents/test-call', { method: 'POST' });
                const data = await res.json();
                out.style.display = 'block';
                if (data.ok) {
                    out.style.color = '#10b981';
                    out.textContent = '\u2713 RESPONSE: ' + data.agent_response + '\\n\\nLOGGED ROW:\\n' + JSON.stringify(data.logged_row, null, 2);
                    await loadCostData();
                } else {
                    out.style.color = '#ef4444';
                    out.textContent = '\u2717 ERROR: ' + data.error;
                }
            } catch(e) {
                out.style.display = 'block';
                out.style.color = '#ef4444';
                out.textContent = '\u2717 Connection failed: ' + e;
            } finally {
                btn.disabled = false;
                btn.textContent = 'RUN TEST CALL';
            }
        }

        loadCostData();
        setInterval(loadCostData, 30000);
    </script>"""

if old_cost_js not in content:
    print("ERROR: Could not find cost JS!")
    idx = content.find('AGENT COST CARD')
    print(f"Found 'AGENT COST CARD' at {idx}")
else:
    content = content.replace(old_cost_js, '', 1)
    print("3. Removed cost card JS")

# ============================================================
# 4. Remove export JS (lines 289-317)
# ============================================================
old_export_js = """    <script>
        window.addEventListener('DOMContentLoaded', function () {
            const t = new Date();
            document.getElementById('exportEndDate').valueAsDate = t;
            const p = new Date(); p.setDate(t.getDate() - 14);
            document.getElementById('exportStartDate').valueAsDate = p;
        });

        async function copyAuditExport() {
            const btn = document.getElementById('exportCopyBtn');
            const status = document.getElementById('exportStatus');
            const start = document.getElementById('exportStartDate').value;
            const end = document.getElementById('exportEndDate').value;
            if (!start || !end) { status.textContent = 'Pick both dates first'; return; }

            status.textContent = 'Fetching\u2026';
            try {
                const res = await fetch(`/admin/export-audit-ledger?start_date=${start}&end_date=${end}`);
                const data = await res.json();
                if (!data.ok) { status.textContent = 'Error: ' + (data.error || 'unknown'); return; }
                await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
                const orig = btn.textContent;
                btn.textContent = '\u2713 COPIED';
                status.textContent = `${data.total_records} trades, ${(data.daily_digests || []).length} digests, ${(data.audit_suggestions || []).length} suggestions, ${(data.trials || []).length} trials`;
                setTimeout(() => { btn.textContent = orig; }, 2000);
            } catch (e) {
                status.textContent = 'Fetch failed: ' + e;
            }
        }
    </script>"""

if old_export_js not in content:
    print("ERROR: Could not find export JS!")
    idx = content.find('copyAuditExport')
    print(f"Found 'copyAuditExport' at {idx}")
else:
    content = content.replace(old_export_js, '', 1)
    print("4. Removed export JS")

# ============================================================
# Write the modified file
# ============================================================
with open('templates/admin.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done! Admin page stripped to user management only.")
