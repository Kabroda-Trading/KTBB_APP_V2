
    // ── ESPRESSO EXECUTIVE CHART PALETTE ──────────────────
    const C = {
        cognac:  '#c8813a',
        green:   '#5e8c66',
        red:     '#8c4a3a',
        amber:   '#a67c3a',
        gold:    '#c9a24a',
        muted:   '#6b5345',
        cream:   '#f2e3cf',
        latte:   '#c9a880',
        leather: '#8a6f57',
        border:  '#3a2820',
        panel:   '#251a14',
    };

    Chart.defaults.color        = C.leather;
    Chart.defaults.borderColor  = C.border;
    Chart.defaults.font.family  = "'JetBrains Mono', monospace";
    Chart.defaults.font.size    = 11;

    const tooltipDefaults = {
        backgroundColor: C.panel,
        titleColor:      C.cream,
        bodyColor:       C.latte,
        borderColor:     C.border,
        borderWidth:     1,
        padding:         10,
    };

    const scalesXY = {
        x: { ticks: { color: C.leather }, grid: { color: C.border } },
        y: { ticks: { color: C.leather }, grid: { color: C.border } },
    };

    // ── KPI OVERVIEW ──────────────────────────────────────
    async function loadOverview() {
        try {
            const r = await fetch('/api/dashboard/overview');
            if (!r.ok) throw new Error("Status " + r.status);
            const d = await r.json();
            if (!d.ok) throw new Error(d.error || "Unknown error");

            document.getElementById('k-sessions').textContent = d.total_sessions;
            document.getElementById('k-approved').textContent = d.approved_rate + '%';
            document.getElementById('k-winrate').textContent  = d.win_rate + '%';

            const netrEl = document.getElementById('k-netr');
            netrEl.textContent = (d.net_r >= 0 ? '+' : '') + d.net_r + 'R';
            netrEl.style.color = d.net_r > 0 ? C.green : (d.net_r < 0 ? C.red : C.cream);

            document.getElementById('k-spend').textContent = '$' + d.spend_7d;
            document.getElementById('k-cache').textContent = d.cache_hit_rate + '%';

            const now = new Date();
            document.getElementById('tsLabel').textContent =
                'Updated ' + now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        } catch (e) {
            console.error("Failed to load overview:", e);
            document.getElementById('k-sessions').textContent = 'ERR';
            document.getElementById('k-approved').textContent = 'ERR';
            document.getElementById('k-winrate').textContent  = 'ERR';
            document.getElementById('k-netr').textContent     = 'ERR';
            document.getElementById('k-netr').style.color     = C.red;
            document.getElementById('k-spend').textContent    = 'ERR';
            document.getElementById('k-cache').textContent    = 'ERR';
            document.getElementById('tsLabel').textContent    = 'Load Error';
            document.getElementById('tsLabel').style.color    = C.red;
        }
    }

    // ── MAS HISTORY: PnL LINE + APPROVAL DONUT + TRADE TABLE ──
    async function loadMasHistory() {
        try {
            const r = await fetch('/api/dashboard/mas-history');
            if (!r.ok) throw new Error("Status " + r.status);
            const d = await r.json();
            if (!d.ok) throw new Error(d.error || "Unknown error");

            // Cumulative PnL line
            new Chart(document.getElementById('chartPnL'), {
                type: 'line',
                data: {
                    labels: d.pnl_series.map(p => p.date),
                    datasets: [{
                        label: 'Cumulative R',
                        data:  d.pnl_series.map(p => p.cumulative),
                        borderColor:     C.cognac,
                        backgroundColor: 'rgba(200,129,58,0.07)',
                        fill:            true,
                        tension:         0.35,
                        pointRadius:     d.pnl_series.length < 30 ? 4 : 2,
                        pointBackgroundColor: C.cognac,
                        borderWidth:     2,
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: true,
                    plugins: { legend: { display: false }, tooltip: tooltipDefaults },
                    scales: scalesXY,
                }
            });

            // Approval donut
            const ap = d.approval_counts;
            new Chart(document.getElementById('chartApproval'), {
                type: 'doughnut',
                data: {
                    labels: ['Approved', 'Rejected', 'Waiting', 'Error/Other'],
                    datasets: [{
                        data: [ap.APPROVED||0, ap.REJECTED||0, ap.WAITING_FOR_15M||0, (ap.MAS_ERROR||0)+(ap.PENDING||0)],
                        backgroundColor: [C.green, C.red, C.amber, C.muted],
                        borderColor:     C.panel,
                        borderWidth:     3,
                    }]
                },
                options: {
                    responsive: true, cutout: '72%',
                    plugins: {
                        legend:  { position: 'bottom', labels: { color: C.leather, padding: 14 } },
                        tooltip: tooltipDefaults,
                    }
                }
            });

            // Trade history table
            const tbody = document.getElementById('tradeBody');
            if (!d.trades.length) {
                tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No trade history yet. Sessions will appear here after the first session lock.</td></tr>';
                return;
            }
            tbody.innerHTML = d.trades.map(t => {
                const pnlStr = t.realized_pnl || '—';
                const pnlCls = t.realized_pnl ? (t.realized_pnl.startsWith('+') ? 's-win' : (t.realized_pnl.startsWith('-') ? 's-loss' : '')) : '';
                const st  = (t.status || '').toLowerCase();
                const mas = (t.mas_approval_status || '').toLowerCase();
                const fmt = v => v ? '$' + Number(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}) : '—';
                return `<tr>
                    <td>${t.date_key || '—'}</td>
                    <td style="color:#f2e3cf;font-weight:800;">${t.bias || '—'}</td>
                    <td><span class="s-${mas}">${t.mas_approval_status || '—'}</span></td>
                    <td><span class="s-${st}">${t.status || '—'}</span></td>
                    <td>${fmt(t.entry_price)}</td>
                    <td>${fmt(t.stop_loss)}</td>
                    <td>${fmt(t.t1)}</td>
                    <td class="${pnlCls}">${pnlStr}</td>
                </tr>`;
            }).join('');
        } catch (e) {
            console.error("Failed to load MAS history:", e);
            document.getElementById('tradeBody').innerHTML = '<tr><td colspan="8" class="empty-state" style="color:#8c4a3a;">Failed to load trade history.</td></tr>';
            document.getElementById('chartPnL').parentElement.innerHTML = `<div style="padding:40px; color:#8c4a3a; font-family:'JetBrains Mono',monospace; font-size:12px; text-align:center;">Failed to load PnL chart data.</div>`;
            document.getElementById('chartApproval').parentElement.innerHTML = `<div style="padding:40px; color:#8c4a3a; font-family:'JetBrains Mono',monospace; font-size:12px; text-align:center;">Failed to load approval status data.</div>`;
        }
    }

    // ── ACCURACY CHARTS ───────────────────────────────────
    async function loadAccuracy() {
        try {
            const r = await fetch('/api/dashboard/accuracy');
            if (!r.ok) throw new Error("Status " + r.status);
            const d = await r.json();
            if (!d.ok) throw new Error(d.error || "Unknown error");

            // Grade accuracy -- N shown in the label itself, not hidden, since
            // this is now thin, record-only 4H/1H data (see main.py comment).
            const ga = d.grade_accuracy;
            const grades = Object.keys(ga);
            new Chart(document.getElementById('chartGrade'), {
                type: 'bar',
                data: {
                    labels: grades.map(g => `${g} (N=${ga[g].total})`),
                    datasets: [
                        { label: 'Correct %',   data: grades.map(g => ga[g].correct_pct),   backgroundColor: C.cognac, borderRadius: 4 },
                        { label: 'Incorrect %', data: grades.map(g => ga[g].incorrect_pct), backgroundColor: C.muted,  borderRadius: 4 },
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: true,
                    plugins: { legend: { labels: { color: C.leather } }, tooltip: tooltipDefaults },
                    scales: {
                        x: { ...scalesXY.x },
                        y: { ...scalesXY.y, max: 100, ticks: { color: C.leather, callback: v => v + '%' } },
                    }
                }
            });

            // Confluence accuracy
            const ca = d.confluence_accuracy;
            const scores = Object.keys(ca).sort((a,b) => Number(a)-Number(b));
            new Chart(document.getElementById('chartConfluence'), {
                type: 'bar',
                data: {
                    labels: scores.map(s => 'Score ' + s),
                    datasets: [
                        { label: 'Correct %',   data: scores.map(s => ca[s].correct_pct),   backgroundColor: C.green,  borderRadius: 4 },
                        { label: 'Incorrect %', data: scores.map(s => ca[s].incorrect_pct), backgroundColor: C.muted,   borderRadius: 4 },
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: true,
                    plugins: { legend: { labels: { color: C.leather } }, tooltip: tooltipDefaults },
                    scales: {
                        x: { ...scalesXY.x },
                        y: { ...scalesXY.y, max: 100, ticks: { color: C.leather, callback: v => v + '%' } },
                    }
                }
            });
        } catch (e) {
            console.error("Failed to load accuracy:", e);
            document.getElementById('chartGrade').parentElement.innerHTML = `<div style="padding:40px; color:#8c4a3a; font-family:'JetBrains Mono',monospace; font-size:12px; text-align:center;">Failed to load grade accuracy.</div>`;
            document.getElementById('chartConfluence').parentElement.innerHTML = `<div style="padding:40px; color:#8c4a3a; font-family:'JetBrains Mono',monospace; font-size:12px; text-align:center;">Failed to load confluence accuracy.</div>`;
        }
    }

    // ── AGENT COSTS (ADMIN ONLY) ──────────────────────────
    const IS_ADMIN = true;

    async function loadCosts() {
        if (!IS_ADMIN) {
            document.getElementById('costCard').style.display = 'none';
            return;
        }
        try {
            const r = await fetch('/api/dashboard/costs');
            if (!r.ok) throw new Error("Status " + r.status);
            const d = await r.json();
            if (!d.ok) throw new Error(d.error || "Unknown error");

            const palette = [C.cognac, C.green, C.gold, C.amber, C.muted, C.red];
            new Chart(document.getElementById('chartCost'), {
                type: 'bar',
                data: {
                    labels: d.days,
                    datasets: d.agents.map((ag, i) => ({
                        label: ag.name,
                        data:  ag.values,
                        backgroundColor: palette[i % palette.length],
                        borderRadius: 2,
                    }))
                },
                options: {
                    responsive: true, maintainAspectRatio: true,
                    plugins: { legend: { labels: { color: C.leather } }, tooltip: tooltipDefaults },
                    scales: {
                        x: { stacked: true, ...scalesXY.x },
                        y: { stacked: true, ...scalesXY.y, ticks: { color: C.leather, callback: v => '$' + v.toFixed(3) } },
                    }
                }
            });
        } catch (e) {
            console.error("Failed to load costs:", e);
            document.getElementById('chartCost').parentElement.innerHTML = `<div style="padding:40px; color:#8c4a3a; font-family:'JetBrains Mono',monospace; font-size:12px; text-align:center;">Failed to load cost data.</div>`;
        }
    }

    // ── AGENT COST MONITOR (detail) ────────────────────────
    async function loadCostData() {
        if (!document.getElementById('costTableBody')) return;
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
            bar.style.background = pct >= 90 ? '#8c4a3a' : pct >= 70 ? '#a67c3a' : '#c8813a';

            const tbody = document.getElementById('costTableBody');
            const calls = data.last_10_calls || [];
            if (calls.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" style="padding:20px; text-align:center; color:#6b5345;">No calls logged yet. Run a test call.</td></tr>';
                return;
            }
            tbody.innerHTML = calls.map(r => {
                const sc = r.status === 'SUCCESS' ? '#5e8c66' : r.status === 'BUDGET_BLOCKED' ? '#a67c3a' : '#8c4a3a';
                const cr = r.cache_read_tokens > 0 ? `<span style="color:#5e8c66">${r.cache_read_tokens}</span>` : '0';
                return `<tr style="border-bottom:1px solid #2a1e18;">
                    <td style="padding:6px 8px; color:#c9a880;">${r.agent_name}</td>
                    <td style="padding:6px 8px; color:#6b5345;">${r.triggered_by}</td>
                    <td style="padding:6px 8px; color:${sc}; font-weight:700;">${r.status}</td>
                    <td style="padding:6px 8px; text-align:right; color:#c9a880;">${r.input_tokens}</td>
                    <td style="padding:6px 8px; text-align:right; color:#c9a880;">${r.output_tokens}</td>
                    <td style="padding:6px 8px; text-align:right;">${cr}</td>
                    <td style="padding:6px 8px; text-align:right; color:#c9a24a;">$${r.estimated_cost_usd.toFixed(6)}</td>
                    <td style="padding:6px 8px; text-align:right; color:#6b5345; font-size:9px;">${(r.created_at || '').replace(' UTC','')}</td>
                </tr>`;
            }).join('');
        } catch (e) {
            console.error('Cost load failed:', e);
        }
    }

    async function runTestCall() {
        const btn = document.getElementById('testBtn');
        const out = document.getElementById('testResult');
        btn.disabled = true;
        btn.textContent = 'FIRING…';
        out.textContent = '';

        try {
            const res = await fetch('/api/agents/test-call', { method: 'POST' });
            const data = await res.json();
            if (data.ok) {
                out.style.color = '#5e8c66';
                out.textContent = '✓ RESPONSE: ' + data.agent_response + '\n\nLOGGED ROW:\n' + JSON.stringify(data.logged_row, null, 2);
                await loadCostData();
            } else {
                out.style.color = '#8c4a3a';
                out.textContent = '✗ ERROR: ' + data.error;
            }
        } catch (e) {
            out.style.color = '#8c4a3a';
            out.textContent = '✗ Connection failed: ' + e;
        } finally {
            btn.disabled = false;
            btn.textContent = 'RUN TEST CALL';
        }
    }

    // ── AUDIT-AI & DATA EXPORT ──────────────────────────────
    async function loadAuditSuggestions() {
        if (!document.getElementById('auditSuggestionsBody')) return;
        try {
            const res = await fetch('/api/v1/system/audit-suggestions');
            const data = await res.json();
            if (!data.ok) return;

            const digest = data.latest_daily_digest;
            const label = document.getElementById('auditDigestLabel');
            const summary = document.getElementById('auditDigestSummary');
            if (digest) {
                label.textContent = 'last digest: ' + digest.date_key;
                summary.innerHTML = `Today's digest (${digest.date_key}): ` +
                    `<b style="color:#f2e3cf;">${digest.trades_covered_15m}</b> 15M &middot; ` +
                    `<b style="color:#f2e3cf;">${digest.trades_covered_1h}</b> 1H &middot; ` +
                    `<b style="color:#f2e3cf;">${digest.trades_covered_4h}</b> 4H trades covered.`;
            } else {
                summary.textContent = 'No daily digest yet — first run fires at 23:45 UTC.';
            }

            const tbody = document.getElementById('auditSuggestionsBody');
            const suggestions = data.recent_suggestions || [];
            if (suggestions.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="padding:20px; text-align:center; color:#6b5345;">No Audit-AI findings logged yet.</td></tr>';
                return;
            }
            tbody.innerHTML = suggestions.map(s => `
                <tr style="border-bottom:1px solid #2a1e18;">
                    <td style="padding:6px 8px; color:#f2e3cf; white-space:nowrap;">${s.hypothesis_id}</td>
                    <td style="padding:6px 8px; color:#c9a880; white-space:nowrap;">${s.tier_label}</td>
                    <td style="padding:6px 8px; color:#c9a880;">${s.n_supporting}</td>
                    <td style="padding:6px 8px; color:#c9a880; font-family:'Inter',sans-serif;">${s.suggestion_text}</td>
                </tr>
            `).join('');
        } catch (e) {
            console.error('loadAuditSuggestions failed:', e);
        }
    }

    async function copyAuditExport() {
        const btn = document.getElementById('exportCopyBtn');
        const status = document.getElementById('exportStatus');
        const start = document.getElementById('exportStartDate').value;
        const end = document.getElementById('exportEndDate').value;
        if (!start || !end) { status.textContent = 'Pick both dates first'; return; }

        status.textContent = 'Fetching…';
        try {
            const res = await fetch(`/admin/export-audit-ledger?start_date=${start}&end_date=${end}`);
            const data = await res.json();
            if (!data.ok) { status.textContent = 'Error: ' + (data.error || 'unknown'); return; }
            await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
            const orig = btn.textContent;
            btn.textContent = '✓ COPIED';
            status.textContent = `${data.total_records} trades, ${(data.daily_digests || []).length} digests, ${(data.audit_suggestions || []).length} suggestions, ${(data.trials || []).length} trials`;
            setTimeout(() => { btn.textContent = orig; }, 2000);
        } catch (e) {
            status.textContent = 'Fetch failed: ' + e;
        }
    }

    // ── JEWEL GATE DONUT ──────────────────────────────────
    async function loadJewel() {
        try {
            const r = await fetch('/api/dashboard/jewel');
            if (!r.ok) throw new Error("Status " + r.status);
            const d = await r.json();
            if (!d.ok) throw new Error(d.error || "Unknown error");
            new Chart(document.getElementById('chartJewel'), {
                type: 'doughnut',
                data: {
                    labels: ['Gate Open + Win', 'Gate Open + Loss', 'Gate Closed + Win', 'Gate Closed + Loss'],
                    datasets: [{
                        data: [d.open_win, d.open_loss, d.closed_win, d.closed_loss],
                        backgroundColor: [C.green, C.red, C.amber, C.muted],
                        borderColor:     C.panel,
                        borderWidth:     3,
                    }]
                },
                options: {
                    responsive: true, cutout: '68%',
                    plugins: {
                        legend:  { position: 'bottom', labels: { color: C.leather, padding: 10, font: { size: 10 } } },
                        tooltip: tooltipDefaults,
                    }
                }
            });
        } catch (e) {
            console.error("Failed to load Jewel:", e);
            document.getElementById('chartJewel').parentElement.innerHTML = `<div style="padding:40px; color:#8c4a3a; font-family:'JetBrains Mono',monospace; font-size:12px; text-align:center;">Failed to load Jewel gate data.</div>`;
        }
    }

    // ── INTERNAL SYSTEM AUDITS ────────────────────────────
    window.auditArchive = {};
    let auditListVisible = true;

    function toggleAuditList() {
        const list     = document.getElementById('auditList');
        const chevron  = document.getElementById('auditChevron');
        auditListVisible = !auditListVisible;
        list.style.display   = auditListVisible ? 'block' : 'none';
        const count = Object.keys(window.auditArchive).length;
        chevron.textContent  = auditListVisible
            ? `▼ ${count} REPORT${count !== 1 ? 'S' : ''}`
            : `▶ ${count} REPORT${count !== 1 ? 'S' : ''} (COLLAPSED)`;
    }

    async function loadAudits() {
        const list    = document.getElementById('auditList');
        const chevron = document.getElementById('auditChevron');

        try {
            const r = await fetch('/api/dashboard/audits');
            const d = await r.json();

            if (!d.ok || !d.audits.length) {
                list.innerHTML = `<div style="padding:20px 0; color:#5a3d2c; font-family:'Playfair Display',serif; font-style:italic; font-size:14px; text-align:center;">No system audits yet. The first will appear after the next Sunday 23:00 UTC scheduler run.</div>`;
                chevron.textContent = '▼ 0 REPORTS';
                return;
            }

            window.auditArchive = {};
            d.audits.forEach(a => { window.auditArchive[a.id] = a; });

            const count = d.audits.length;
            chevron.textContent = `▼ ${count} REPORT${count !== 1 ? 'S' : ''}`;

            list.innerHTML = d.audits.map((a, idx) => {
                // First non-empty line as preview title
                const firstLine = (a.audit_md || '')
                    .split('\n')
                    .map(l => l.trim())
                    .find(l => l.length > 0) || 'System Audit';
                const preview = firstLine.replace(/^[0-9]+\.\s*/, '').slice(0, 90);
                const isLast  = idx === count - 1;

                return `
                <div onclick="openAuditModal(${a.id})"
                     style="display:flex; align-items:center; gap:16px; padding:14px 20px;
                            background:#251a14; border:1px solid #4d3526;
                            border-top:${idx === 0 ? '1px solid #4d3526' : 'none'};
                            border-radius:${idx === 0 ? '0 0 0 0' : '0'};
                            ${isLast ? 'border-radius:0 0 8px 8px;' : ''}
                            cursor:pointer; transition:background 0.15s;"
                     onmouseover="this.style.background='#2d1e15'"
                     onmouseout="this.style.background='#251a14'">
                    <div style="font-family:'JetBrains Mono',monospace; font-size:10px;
                                color:#6b5345; min-width:90px; letter-spacing:1px;">
                        ${a.date_key}
                    </div>
                    <div style="flex:1; font-family:'Inter',sans-serif; font-size:13px;
                                color:#c9a880; overflow:hidden; text-overflow:ellipsis;
                                white-space:nowrap;">
                        ${preview}
                    </div>
                    <div style="font-family:'JetBrains Mono',monospace; font-size:11px;
                                color:#6b5345;">›</div>
                </div>`;
            }).join('');

        } catch(e) {
            list.innerHTML = `<div style="padding:20px; color:#8c4a3a; font-family:'JetBrains Mono',monospace; font-size:12px;">Failed to load audits.</div>`;
            chevron.textContent = '▼ ERROR';
        }
    }

    function openAuditModal(id) {
        const a = window.auditArchive[id];
        if (!a) return;
        
        const parts = [
            'SYSTEM AUDIT',
            a.date_key || '—',
            a.created_at ? a.created_at.slice(0, 16).replace('T', ' ') + ' UTC' : ''
        ].filter(Boolean);
        document.getElementById('nlModalMeta').textContent = parts.join('  ·  ');
        document.getElementById('nlModalHeadline').textContent = 'Systemic Adviser Report — ' + (a.date_key || '');
        document.getElementById('nlModalBody').innerHTML       = marked.parse(a.audit_md || '(No content)');
        document.getElementById('nlModal').style.display       = 'block';
        document.body.style.overflow = 'hidden';
    }

    // ── NEWSLETTERS TABLE ─────────────────────────────────
    window.nlArchive = {};

    async function loadNewsletters() {
        try {
            const r = await fetch('/api/dashboard/newsletters');
            const d = await r.json();
            if (!d.ok) return;
            const tbody = document.getElementById('newsletterBody');
            if (!d.newsletters.length) {
                tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No newsletters generated yet. The first will appear here after the next session lock.</td></tr>';
                return;
            }
            window.nlArchive = {};
            d.newsletters.forEach(n => { window.nlArchive[n.id] = n; });
            tbody.innerHTML = d.newsletters.map(n => {
                const apSt = (n.approval_status || '').toLowerCase();
                const puSt = (n.publish_status  || '').toLowerCase();
                return `<tr style="cursor:pointer;" onclick="openNlModal(${n.id})" title="Click to read full newsletter">
                    <td>${n.date_key || '—'}</td>
                    <td style="color:#f2e3cf; max-width:420px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${n.headline || '—'}</td>
                    <td><span class="s-${apSt}">${n.approval_status || '—'}</span></td>
                    <td><span class="s-${puSt}">${n.publish_status || '—'}</span></td>
                </tr>`;
            }).join('');
        } catch (e) {
            console.error("Failed to load newsletters:", e);
            document.getElementById('newsletterBody').innerHTML = '<tr><td colspan="4" class="empty-state" style="color:#8c4a3a;">Failed to load newsletters.</td></tr>';
        }
    }

    function openNlModal(id) {
        const n = window.nlArchive[id];
        if (!n) return;
        
        const parts = [
            n.date_key || '—',
            n.approval_status || '',
            n.publish_status || ''
        ].filter(Boolean);
        document.getElementById('nlModalMeta').textContent = parts.join('  ·  ');
        document.getElementById('nlModalHeadline').textContent = n.headline || '(No headline)';
        document.getElementById('nlModalBody').innerHTML       = marked.parse(n.newsletter_md || '(No content available)');
        document.getElementById('nlModal').style.display       = 'block';
        document.body.style.overflow = 'hidden';
    }

    function closeNlModal() {
        document.getElementById('nlModal').style.display = 'none';
        document.body.style.overflow = 'auto';
    }

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') closeNlModal();
    });

    // ── TAB SWITCHING ────────────────────────────────────
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.tab-btn');
        if (!btn) return;
        const tab = btn.dataset.tab;
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        const panel = document.getElementById('tab-' + tab);
        panel.classList.add('active');
        // Workaround for a confirmed 0x0 bounding-rect render collapse on
        // newly-active tab panels that live browser inspection (computed
        // style, DOM content, CSS cascade -- all correct) could not trace
        // to a root cause. Forcing a synchronous reflow here, plus the
        // min-height on .tab-content.active above, are defensive measures,
        // not a diagnosed fix. See WORK_LOG.md 2026-07-16 entry.
        void panel.offsetHeight;
    });

    // ── LIVE SYSTEM TAB ──────────────────────────────────
    async function loadLiveSystem() {
        try {
            const r = await fetch('/api/v1/system/state');
            if (!r.ok) throw new Error('Status ' + r.status);
            const d = await r.json();
            if (!d.ok) throw new Error(d.error || 'Unknown error');

            // Active sessions
            const sessBody = document.getElementById('liveSessionsBody');
            if (!d.active_sessions || !d.active_sessions.length) {
                sessBody.innerHTML = '<tr><td colspan="4" class="empty-state">No active sessions.</td></tr>';
            } else {
                sessBody.innerHTML = d.active_sessions.map(s => `<tr>
                    <td>${s.symbol || '—'}</td>
                    <td>${s.session_id || '—'}</td>
                    <td>${s.date_key || '—'}</td>
                    <td>${s.lock_time || '—'}</td>
                </tr>`).join('');
            }

            // Active runners (from scheduler_health)
            const runBody = document.getElementById('liveRunnersBody');
            const sh = d.scheduler_health || {};
            const runnerKeys = Object.keys(sh);
            if (!runnerKeys.length) {
                runBody.innerHTML = '<tr><td colspan="5" class="empty-state">No runner data.</td></tr>';
            } else {
                runBody.innerHTML = runnerKeys.map(k => {
                    const v = sh[k] || {};
                    const st = v.status || 'UNKNOWN';
                    const stCls = st === 'WAITING' ? 's-approved' : (st === 'ERROR' ? 's-failed' : '');
                    return `<tr>
                        <td style="color:#f2e3cf;font-weight:800;">${k}</td>
                        <td><span class="${stCls}">${st}</span></td>
                        <td>${v.last_run ? v.last_run.slice(0,19).replace('T',' ') : '—'}</td>
                        <td>${v.next_run ? v.next_run.slice(0,19).replace('T',' ') : '—'}</td>
                        <td>${v.error_count || 0}</td>
                    </tr>`;
                }).join('');
            }

            // Macro engine
            const macroBody = document.getElementById('macroEngineBody');
            const me = d.macro_engine || {};
            macroBody.innerHTML = `<tr>
                <td>${me.symbol || 'BTC/USDT'}</td>
                <td style="color:#f2e3cf;font-weight:800;">${me.latest_bias || 'UNKNOWN'}</td>
                <td><span class="${me.active ? 's-approved' : 's-failed'}">${me.active ? 'ACTIVE' : 'INACTIVE'}</span></td>
            </tr>`;
        } catch (e) {
            console.error('Failed to load live system:', e);
            document.getElementById('liveSessionsBody').innerHTML = '<tr><td colspan="4" class="empty-state" style="color:#8c4a3a;">Failed to load.</td></tr>';
            document.getElementById('liveRunnersBody').innerHTML = '<tr><td colspan="5" class="empty-state" style="color:#8c4a3a;">Failed to load.</td></tr>';
            document.getElementById('macroEngineBody').innerHTML = '<tr><td colspan="3" class="empty-state" style="color:#8c4a3a;">Failed to load.</td></tr>';
        }
    }

    // ── PARAMETERS TAB ────────────────────────────────────
    async function loadParameters() {
        try {
            const r = await fetch('/api/v1/system/parameters');
            if (!r.ok) throw new Error('Status ' + r.status);
            const d = await r.json();
            if (!d.ok) throw new Error(d.error || 'Unknown error');

            const paramsBody = document.getElementById('paramsBody');
            if (!d.parameters || !d.parameters.length) {
                paramsBody.innerHTML = '<tr><td colspan="5" class="empty-state">No parameters registered.</td></tr>';
            } else {
                paramsBody.innerHTML = d.parameters.map(p => `<tr>
                    <td style="color:#f2e3cf;font-weight:800;">${p.name || '—'}</td>
                    <td style="color:#c9a24a;font-weight:800;">${p.value || '—'}</td>
                    <td>${p.description || '—'}</td>
                    <td>${p.source || '—'}</td>
                    <td>${p.last_updated ? p.last_updated.slice(0,19).replace('T',' ') : '—'}</td>
                </tr>`).join('');
            }

            const depsBody = document.getElementById('depsBody');
            if (!d.dependencies || !d.dependencies.length) {
                depsBody.innerHTML = '<tr><td colspan="3" class="empty-state">No dependencies registered.</td></tr>';
            } else {
                depsBody.innerHTML = d.dependencies.map(dp => `<tr>
                    <td style="color:#f2e3cf;font-weight:800;">${dp.name || '—'}</td>
                    <td>${dp.depends_on || '—'}</td>
                    <td>${dp.relationship_type || '—'}</td>
                </tr>`).join('');
            }
        } catch (e) {
            console.error('Failed to load parameters:', e);
            document.getElementById('paramsBody').innerHTML = '<tr><td colspan="5" class="empty-state" style="color:#8c4a3a;">Failed to load.</td></tr>';
            document.getElementById('depsBody').innerHTML = '<tr><td colspan="3" class="empty-state" style="color:#8c4a3a;">Failed to load.</td></tr>';
        }
    }

    // ── SESSION ENERGY TAB ───────────────────────────────────
    async function loadSessionEnergy() {
        try {
            const r = await fetch('/api/v1/system/session-energy');
            if (!r.ok) throw new Error('Status ' + r.status);
            const d = await r.json();
            if (!d.ok) throw new Error(d.error || 'Unknown error');
            if (!d.has_data) {
                document.getElementById('energySessionLabel').textContent = 'No data yet';
                return;
            }

            // Session label
            const sess = d.session || {};
            document.getElementById('energySessionLabel').textContent = (sess.label || '?') + ' - ' + (sess.date_key || '?');

            // Helper: set fuel gauge fields
            function setFuel(tf, data) {
                if (!data) return;
                const prefix = 'fuel' + tf;
                const trend = data.trend || '-';
                const momentum = data.momentum || '-';
                const adx = data.adx != null ? data.adx.toFixed(1) : '-';
                const rsi = data.rsi != null ? data.rsi.toFixed(1) : '-';
                const spread = data.ribbon_spread_pct != null ? (data.ribbon_spread_pct * 100).toFixed(2) + '%' : '-';
                const dev = data.deviation != null ? data.deviation.toFixed(2) + '%' : '-';

                document.getElementById(prefix + 'Trend').textContent = trend;
                document.getElementById(prefix + 'Momentum').textContent = momentum;
                document.getElementById(prefix + 'Adx').textContent = adx;
                document.getElementById(prefix + 'Rsi').textContent = rsi;
                document.getElementById(prefix + 'Spread').textContent = spread;
                document.getElementById(prefix + 'Dev').textContent = dev;

                // Color trend by direction
                const trendEl = document.getElementById(prefix + 'Trend');
                if (trend === 'BULLISH' || trend === 'BULLISH_TRENDING') trendEl.style.color = '#5e8c66';
                else if (trend === 'BEARISH' || trend === 'BEARISH_TRENDING') trendEl.style.color = '#8c4a3a';
                else trendEl.style.color = '#c9a24a';

                // Exit warning
                const exitEl = document.getElementById(prefix + 'Exit');
                if (data.exit_warning) {
                    exitEl.textContent = '! ' + data.exit_warning;
                    exitEl.style.display = 'block';
                } else {
                    exitEl.style.display = 'none';
                }

                // Status line
                document.getElementById(prefix + 'Status').textContent = 'ADX ' + adx + ' . RSI ' + rsi;
            }

            setFuel('15m', d.fuel_gauge ? d.fuel_gauge['15m'] : null);
            setFuel('1h', d.fuel_gauge ? d.fuel_gauge['1h'] : null);
            setFuel('4h', d.fuel_gauge ? d.fuel_gauge['4h'] : null);

            // Bias model
            const bm = d.bias_model || {};
            // daily_lean/permission_state are nested objects ({direction/score/confidence},
            // {state/active_side/...}), not plain strings -- pull the one field worth showing.
            const dailyLean = bm.daily_lean || {};
            document.getElementById('energyDailyLean').textContent = dailyLean.direction
                ? dailyLean.direction.toUpperCase() + ' (' + dailyLean.confidence + '%)'
                : '-';
            const permissionState = bm.permission_state || {};
            document.getElementById('energyPermission').textContent = permissionState.state || '-';
            document.getElementById('energyMacroBias').textContent = d.macro_bias || '-';
            document.getElementById('energyMicroBias').textContent = d.micro_bias || '-';
            document.getElementById('energyMicroState').textContent = d.micro_state || '-';

            // Jewel gate
            const jg = document.getElementById('energyJewelGate');
            if (d.jewel_gate_open === true) {
                jg.textContent = 'OPEN';
                jg.style.color = '#5e8c66';
            } else if (d.jewel_gate_open === false) {
                jg.textContent = 'CLOSED';
                jg.style.color = '#8c4a3a';
            } else {
                jg.textContent = '-';
                jg.style.color = '#f2e3cf';
            }

            // Levels
            const lv = d.levels || {};
            document.getElementById('energyBo').textContent = lv.breakout_trigger ? '$' + lv.breakout_trigger.toFixed(2) : '-';
            document.getElementById('energyBd').textContent = lv.breakdown_trigger ? '$' + lv.breakdown_trigger.toFixed(2) : '-';
            document.getElementById('energyPoc').textContent = lv.f24_poc ? '$' + lv.f24_poc.toFixed(2) : '-';
            document.getElementById('energyR30h').textContent = lv.range30m_high ? '$' + lv.range30m_high.toFixed(2) : '-';
            document.getElementById('energyR30l').textContent = lv.range30m_low ? '$' + lv.range30m_low.toFixed(2) : '-';
            document.getElementById('energyAtr').textContent = lv.atr ? '$' + lv.atr.toFixed(2) : '-';
            document.getElementById('energyEma20').textContent = lv.daily_ema20 ? '$' + lv.daily_ema20.toFixed(2) : '-';
            document.getElementById('energyEma30').textContent = lv.daily_ema30 ? '$' + lv.daily_ema30.toFixed(2) : '-';
            document.getElementById('energyEma50').textContent = lv.daily_ema50 ? '$' + lv.daily_ema50.toFixed(2) : '-';

            // Bias sub
            document.getElementById('energyBiasSub').textContent = 'Price: ' + (d.current_price ? '$' + d.current_price.toFixed(2) : '-') + ' . Lock: ' + (d.lock_time || '-');

        } catch (e) {
            console.error('Failed to load session energy:', e);
            document.getElementById('energySessionLabel').textContent = 'Failed to load';
        }
    }

    // ── SYSTEM HEALTH (heartbeat) ─────────────────────────────
    async function loadHeartbeat() {
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
                auditDt.textContent = 'last: ' + (sal.last_date_key || '-') + ' (' + (sal.last_status || '-') + ')  |  recent: ' + (sal.recent_count ?? '-') + ' rows';
            } else if (sal.status === 'TABLE_MISSING') {
                auditDot.style.background = '#ef4444';
                auditSt.style.color = '#ef4444';
                auditSt.textContent = 'TABLE MISSING';
                auditDt.textContent = sal.error || '';
            } else {
                auditDot.style.background = '#f59e0b';
                auditSt.style.color = '#f59e0b';
                auditSt.textContent = 'DARK';
                auditDt.textContent = 'Table exists - no rows yet';
            }

            const monDot = document.getElementById('hbMonDot');
            const monSt  = document.getElementById('hbMonStatus');
            const monDt  = document.getElementById('hbMonDetail');
            if (mel.status === 'WRITING') {
                monDot.style.background = '#10b981';
                monSt.style.color = '#10b981';
                monSt.textContent = 'WRITING';
                monDt.textContent = 'last session: ' + (mel.last_session_date || '-') + '  poll seq: ' + (mel.last_poll_seq ?? '-') + '  |  recent: ' + (mel.recent_count ?? '-') + ' rows';
            } else if (mel.status === 'TABLE_MISSING') {
                monDot.style.background = '#ef4444';
                monSt.style.color = '#ef4444';
                monSt.textContent = 'TABLE MISSING';
                monDt.textContent = mel.error || '';
            } else {
                monDot.style.background = '#f59e0b';
                monSt.style.color = '#f59e0b';
                monSt.textContent = 'DARK';
                monDt.textContent = 'Table exists - no rows yet';
            }
        } catch (e) {
            console.warn('Heartbeat fetch failed:', e);
        }
    }


    // ── ERRORS TAB ───────────────────────────────────────
    async function loadErrors() {
        try {
            const r = await fetch('/api/v1/system/errors');
            if (!r.ok) throw new Error('Status ' + r.status);
            const d = await r.json();
            if (!d.ok) throw new Error(d.error || 'Unknown error');

            // Health summary
            const hs = d.health_summary || {};
            document.getElementById('healthScore').textContent = hs.overall_health_score ?? '—';
            document.getElementById('healthStatus').textContent = hs.system_ok ? 'OK' : 'ISSUES';
            document.getElementById('healthStatus').style.color = hs.system_ok ? '#5e8c66' : '#8c4a3a';
            document.getElementById('healthSummary').textContent = hs.system_ok
                ? 'All systems operational.'
                : `${(d.errors || []).length} error(s) detected.`;

            // Alert history
            const alerts = d.alert_history || [];
            document.getElementById('alertHistory').innerHTML = alerts.length
                ? alerts.map(a => `<div style="padding:4px 0; border-bottom:1px solid #3a2820;">${a.message ? a.message.slice(0,80) : 'Unknown alert'}</div>`).join('')
                : '<div style="color:#6b5345;">No critical alerts.</div>';

            // Error log
            const errBody = document.getElementById('errorsBody');
            if (!d.errors || !d.errors.length) {
                errBody.innerHTML = '<tr><td colspan="5" class="empty-state">No errors recorded.</td></tr>';
            } else {
                errBody.innerHTML = d.errors.map(e => {
                    const typeCls = e.error_type === 'critical' ? 's-failed' : (e.error_type === 'warning' ? 's-waiting' : '');
                    return `<tr>
                        <td>${e.id || '—'}</td>
                        <td>${e.timestamp ? e.timestamp.slice(0,19).replace('T',' ') : '—'}</td>
                        <td><span class="${typeCls}">${e.error_type || '—'}</span></td>
                        <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${e.message ? e.message.slice(0,100) : '—'}</td>
                        <td>${e.resolved ? '✅' : '❌'}</td>
                    </tr>`;
                }).join('');
            }
        } catch (e) {
            console.error('Failed to load errors:', e);
            document.getElementById('healthScore').textContent = 'ERR';
            document.getElementById('healthStatus').textContent = 'ERR';
            document.getElementById('errorsBody').innerHTML = '<tr><td colspan="5" class="empty-state" style="color:#8c4a3a;">Failed to load.</td></tr>';
        }
    }

    // ── ANALYSIS TAB ──────────────────────────────────────
    async function triggerAnalysis() {
        const btn = document.getElementById('triggerAnalysisBtn');
        const result = document.getElementById('analysisResult');
        btn.disabled = true;
        btn.style.opacity = '0.5';
        result.textContent = 'Running analysis…';
        result.style.color = '#c9a880';
        try {
            const r = await fetch('/api/v1/system/analysis/trigger', { method: 'POST' });
            const d = await r.json();
            if (d.status === 'running') {
                result.innerHTML = `<span style="color:#5e8c66;">✓ Analysis complete.</span> Parameters evaluated: ${d.parameters_evaluated}. Last run: ${d.last_run_timestamp ? d.last_run_timestamp.slice(0,19).replace('T',' ') : '—'}`;
            } else {
                result.innerHTML = `<span style="color:#8c4a3a;">✗ ${d.error || 'Unknown error'}</span>`;
            }
        } catch (e) {
            result.innerHTML = `<span style="color:#8c4a3a;">✗ Request failed: ${e.message}</span>`;
        }
        btn.disabled = false;
        btn.style.opacity = '1';
        loadRecentReports();
    }

    async function loadRecentReports() {
        try {
            const r = await fetch('/api/v1/system/analysis/recent');
            if (!r.ok) throw new Error('Status ' + r.status);
            const d = await r.json();
            const container = document.getElementById('recentReports');
            if (!d.reports || !d.reports.length) {
                container.innerHTML = '<div style="color:#6b5345;">No reports yet. Trigger an analysis above.</div>';
            } else {
                container.innerHTML = d.reports.map(rp => `<div style="padding:6px 0; border-bottom:1px solid #3a2820; display:flex; justify-content:space-between;">
                    <span>${rp.query || 'Analysis'}</span>
                    <span style="color:#6b5345;">${rp.status || '—'}</span>
                </div>`).join('');
            }
        } catch (e) {
            document.getElementById('recentReports').innerHTML = '<div style="color:#6b5345;">No reports yet.</div>';
        }
    }

    // ── INIT ──────────────────────────────────────────────
    async function initDashboard() {
        const exportEndEl = document.getElementById('exportEndDate');
        const exportStartEl = document.getElementById('exportStartDate');
        if (exportEndEl && exportStartEl) {
            const t = new Date();
            exportEndEl.valueAsDate = t;
            const p = new Date(); p.setDate(t.getDate() - 14);
            exportStartEl.valueAsDate = p;
        }
        await Promise.all([
            loadOverview().catch(e => console.error("loadOverview failed", e)),
            loadMasHistory().catch(e => console.error("loadMasHistory failed", e)),
            loadAccuracy().catch(e => console.error("loadAccuracy failed", e)),
            loadCosts().catch(e => console.error("loadCosts failed", e)),
            loadCostData().catch(e => console.error("loadCostData failed", e)),
            loadJewel().catch(e => console.error("loadJewel failed", e)),
            loadAudits().catch(e => console.error("loadAudits failed", e)),
            loadNewsletters().catch(e => console.error("loadNewsletters failed", e)),
            loadLiveSystem().catch(e => console.error("loadLiveSystem failed", e)),
            loadSessionEnergy().catch(e => console.error("loadSessionEnergy failed", e)),
            loadHeartbeat().catch(e => console.error("loadHeartbeat failed", e)),
            loadParameters().catch(e => console.error("loadParameters failed", e)),
            loadErrors().catch(e => console.error("loadErrors failed", e)),
            loadRecentReports().catch(e => console.error("loadRecentReports failed", e)),
            loadSignalAccuracy().catch(e => console.error("loadSignalAccuracy failed", e)),
        ]);
    }

    // ── SIGNAL ACCURACY TAB ────────────────────────────────
    async function triggerSignalAccuracy() {
        const result = document.getElementById('saResult');
        result.textContent = 'Running accuracy check…';
        result.style.color = '#c9a880';
        try {
            const r = await fetch('/api/v1/system/signal-accuracy/trigger', { method: 'POST' });
            const d = await r.json();
            if (d.ok) {
                result.innerHTML = `<span style="color:#5e8c66;">✓ Captured ${d.result.total} new signals</span>`;
            } else {
                result.innerHTML = `<span style="color:#8c4a3a;">✗ ${d.error || 'Unknown error'}</span>`;
            }
        } catch (e) {
            result.innerHTML = `<span style="color:#8c4a3a;">✗ Request failed: ${e.message}</span>`;
        }
        loadSignalAccuracy();
    }

    async function loadSignalAccuracy() {
        const signalName = document.getElementById('saSignalFilter')?.value || '';
        const params = new URLSearchParams({ days: '7' });
        if (signalName) params.set('signal_name', signalName);

        try {
            const r = await fetch('/api/v1/system/signal-accuracy?' + params.toString());
            if (!r.ok) throw new Error('Status ' + r.status);
            const d = await r.json();

            // Summary card
            const summary = document.getElementById('saSummary');
            if (!d.stats || !d.stats.length) {
                summary.innerHTML = '<div style="color:#6b5345;">No signal data yet. Run an accuracy check above.</div>';
            } else {
                const totalSignals = d.stats.reduce((s, x) => s + x.total, 0);
                const totalCorrect = d.stats.reduce((s, x) => s + x.correct, 0);
                const overallPct = totalSignals > 0 ? (totalCorrect / totalSignals * 100).toFixed(1) : '—';
                const best = d.stats.reduce((a, b) => a.accuracy_pct > b.accuracy_pct ? a : b, d.stats[0]);
                const worst = d.stats.reduce((a, b) => a.accuracy_pct < b.accuracy_pct ? a : b, d.stats[0]);

                summary.innerHTML = `
                    <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-top:8px;">
                        <div style="background:#1a120e; border:1px solid #4d3526; border-radius:6px; padding:12px; text-align:center;">
                            <div style="color:#8c6a4a; font-size:10px; letter-spacing:1px;">TOTAL SIGNALS</div>
                            <div style="color:#c9a880; font-size:22px; font-weight:700; margin-top:4px;">${totalSignals}</div>
                        </div>
                        <div style="background:#1a120e; border:1px solid #4d3526; border-radius:6px; padding:12px; text-align:center;">
                            <div style="color:#8c6a4a; font-size:10px; letter-spacing:1px;">OVERALL ACCURACY</div>
                            <div style="color:${overallPct >= 50 ? '#5e8c66' : '#8c4a3a'}; font-size:22px; font-weight:700; margin-top:4px;">${overallPct}%</div>
                        </div>
                        <div style="background:#1a120e; border:1px solid #4d3526; border-radius:6px; padding:12px; text-align:center;">
                            <div style="color:#8c6a4a; font-size:10px; letter-spacing:1px;">BEST / WORST</div>
                            <div style="color:#5e8c66; font-size:13px; font-weight:600; margin-top:4px;">${best.signal_name}: ${best.accuracy_pct}%</div>
                            <div style="color:#8c4a3a; font-size:13px; font-weight:600;">${worst.signal_name}: ${worst.accuracy_pct}%</div>
                        </div>
                    </div>
                `;
            }

            // Table
            const tbody = document.getElementById('saTableBody');
            if (!d.stats || !d.stats.length) {
                tbody.innerHTML = '<tr><td colspan="7" style="padding:20px; text-align:center; color:#6b5345;">No signal data yet.</td></tr>';
            } else {
                tbody.innerHTML = d.stats.map(s => {
                    const pct = s.accuracy_pct;
                    const color = pct >= 60 ? '#5e8c66' : pct >= 40 ? '#c9a880' : '#8c4a3a';
                    return `<tr style="border-bottom:1px solid #2a1e18;">
                        <td style="padding:8px 12px; color:#c9a880;">${s.signal_name}</td>
                        <td style="padding:8px 12px; color:#8c6a4a;">${s.signal_value || '—'}</td>
                        <td style="padding:8px 12px; text-align:right; color:#c9a880;">${s.total}</td>
                        <td style="padding:8px 12px; text-align:right; color:#5e8c66;">${s.correct}</td>
                        <td style="padding:8px 12px; text-align:right; color:#8c4a3a;">${s.incorrect}</td>
                        <td style="padding:8px 12px; text-align:right; color:#6b5345;">${s.neutral}</td>
                        <td style="padding:8px 12px; text-align:right; color:${color}; font-weight:700;">${pct}%</td>
                    </tr>`;
                }).join('');
            }
        } catch (e) {
            document.getElementById('saSummary').innerHTML = '<div style="color:#8c4a3a;">Failed to load signal data.</div>';
        }
    }

    document.addEventListener('DOMContentLoaded', initDashboard);

    // =========================================================================
    // PHASE 2: ADAPTIVE SIGNAL TUNING LOOP — Dashboard Functions
    // =========================================================================

    async function loadSignalAlerts() {
        const container = document.getElementById('signalAlertsContainer');
        try {
            const r = await fetch('/api/v1/system/alerts');
            if (!r.ok) throw new Error('Status ' + r.status);
            const d = await r.json();

            if (!d.alerts || !d.alerts.length) {
                container.innerHTML = '<div style="color:#5e8c66;">✓ No signal degradation alerts</div>';
                return;
            }

            container.innerHTML = d.alerts.map(a => {
                const severityColor = a.severity === 'CRITICAL' ? '#8c4a3a' : '#c9a24a';
                const pathLabel = a.experiment_path === 'AUTO' ? 'AUTO' : 'FLAG';
                return `<div style="border-left:3px solid ${severityColor}; padding:8px 12px; margin-bottom:8px; background:#1a120e; border-radius:4px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="color:${severityColor}; font-weight:700;">${a.alert_type}</span>
                        <span style="color:#6b5345; font-size:10px;">${a.created_at ? new Date(a.created_at).toLocaleString() : ''}</span>
                    </div>
                    <div style="color:#c9a880; margin-top:4px;">${a.message}</div>
                    <div style="display:flex; gap:12px; margin-top:4px; font-size:10px; color:#6b5345;">
                        <span>Accuracy: ${a.accuracy_pct}%</span>
                        <span>Samples: ${a.sample_count}</span>
                        <span>Path: ${pathLabel}</span>
                        <span style="cursor:pointer; color:#c9a24a;" onclick="resolveAlert(${a.id})">[Resolve]</span>
                    </div>
                </div>`;
            }).join('');
        } catch (e) {
            container.innerHTML = '<div style="color:#8c4a3a;">Failed to load signal alerts.</div>';
        }
    }

    async function resolveAlert(alertId) {
        try {
            const r = await fetch(`/api/v1/system/alerts/${alertId}/resolve`, { method: 'POST' });
            const d = await r.json();
            if (d.ok) loadSignalAlerts();
        } catch (e) {
            console.error('Resolve alert failed:', e);
        }
    }

    async function loadSignalWeights() {
        const container = document.getElementById('signalWeightsContainer');
        try {
            const r = await fetch('/api/v1/system/signal-weights');
            if (!r.ok) throw new Error('Status ' + r.status);
            const d = await r.json();

            if (!d.weights || !d.weights.length) {
                container.innerHTML = '<div style="color:#6b5345;">No signal weights yet. Run a flagging tick.</div>';
                return;
            }

            container.innerHTML = d.weights.map(w => {
                const weightColor = w.weight >= 1.0 ? '#5e8c66' : w.weight >= 0.5 ? '#c9a24a' : '#8c4a3a';
                const quarantineBadge = w.is_quarantined ? '<span style="color:#8c4a3a; font-size:9px; margin-left:6px;">[QUARANTINED]</span>' : '';
                return `<div style="display:flex; justify-content:space-between; padding:6px 8px; border-bottom:1px solid #2a1e18;">
                    <span style="color:#c9a880;">${w.signal_name}${w.signal_value ? ': ' + w.signal_value : ''}${quarantineBadge}</span>
                    <span style="color:${weightColor}; font-weight:700;">${w.weight.toFixed(2)}x</span>
                </div>`;
            }).join('');
        } catch (e) {
            container.innerHTML = '<div style="color:#8c4a3a;">Failed to load signal weights.</div>';
        }
    }

    async function loadAccuracyReport() {
        const container = document.getElementById('accuracyReportContainer');
        try {
            const r = await fetch('/api/v1/system/accuracy-report');
            if (!r.ok) throw new Error('Status ' + r.status);
            const d = await r.json();

            if (!d.report || !d.report.report_data) {
                container.innerHTML = '<div style="color:#6b5345;">No accuracy report yet. Generate one above.</div>';
                return;
            }

            const data = d.report.report_data;
            const overall = data.overall || {};
            const top5 = data.top_5 || [];
            const bottom5 = data.bottom_5 || [];
            const flagged = data.flagged_signals || {};

            let html = `<div style="margin-bottom:8px; color:#c9a880; font-size:11px;">${data.summary || ''}</div>`;

            if (top5.length) {
                html += '<div style="color:#5e8c66; font-size:10px; font-weight:700; margin-top:8px;">TOP PERFORMERS</div>';
                html += top5.map(s => `<div style="display:flex; justify-content:space-between; padding:3px 0; font-size:10px;">
                    <span style="color:#c9a880;">${s.signal_name}${s.signal_value ? ': ' + s.signal_value : ''}</span>
                    <span style="color:#5e8c66;">${s.accuracy_pct}% (${s.sample_count})</span>
                </div>`).join('');
            }

            if (bottom5.length) {
                html += '<div style="color:#8c4a3a; font-size:10px; font-weight:700; margin-top:8px;">BOTTOM PERFORMERS</div>';
                html += bottom5.map(s => `<div style="display:flex; justify-content:space-between; padding:3px 0; font-size:10px;">
                    <span style="color:#c9a880;">${s.signal_name}${s.signal_value ? ': ' + s.signal_value : ''}</span>
                    <span style="color:#8c4a3a;">${s.accuracy_pct}% (${s.sample_count})</span>
                </div>`).join('');
            }

            if (flagged.total_flagged) {
                html += '<div style="color:#c9a24a; font-size:10px; font-weight:700; margin-top:8px;">FLAGGED FOR REVIEW</div>';
                html += `<div style="display:flex; justify-content:space-between; font-size:10px; padding:3px 0;">
                    <span style="color:#c9a880;">${flagged.total_flagged} signals flagged</span>
                    <span style="color:#8c4a3a;">${flagged.critical || 0} critical</span>
                    <span style="color:#c9a24a;">${flagged.warnings || 0} warnings</span>
                </div>`;
                if (flagged.signals && flagged.signals.length) {
                    html += `<div style="font-size:10px; color:#6b5345; margin-top:2px;">${flagged.signals.join(', ')}</div>`;
                }
            }

            container.innerHTML = html;
        } catch (e) {
            container.innerHTML = '<div style="color:#8c4a3a;">Failed to load accuracy report.</div>';
        }
    }

    async function triggerAccuracyReport() {
        const container = document.getElementById('accuracyReportContainer');
        container.innerHTML = '<div style="color:#c9a880;">Generating report…</div>';
        try {
            const r = await fetch('/api/v1/system/accuracy-report/trigger', { method: 'POST' });
            const d = await r.json();
            if (d.ok) {
                container.innerHTML = '<div style="color:#5e8c66;">✓ Report generated</div>';
            } else {
                container.innerHTML = `<div style="color:#8c4a3a;">✗ ${d.error || 'Failed'}</div>`;
            }
        } catch (e) {
            container.innerHTML = `<div style="color:#8c4a3a;">✗ ${e.message}</div>`;
        }
        loadAccuracyReport();
    }

    async function triggerFlaggingTick() {
        const result = document.getElementById('flaggingResult');
        result.textContent = 'Running flagging tick…';
        result.style.color = '#c9a880';
        try {
            const r = await fetch('/api/v1/system/flagging/trigger', { method: 'POST' });
            const d = await r.json();
            if (d.ok) {
                result.innerHTML = `<span style="color:#5e8c66;">✓ ${d.result.alerts_created} alerts, ${d.result.weights_adjusted} weights adjusted</span>`;
            } else {
                result.innerHTML = `<span style="color:#8c4a3a;">✗ ${d.error || 'Unknown error'}</span>`;
            }
        } catch (e) {
            result.innerHTML = `<span style="color:#8c4a3a;">✗ Request failed: ${e.message}</span>`;
        }
        loadSignalAlerts();
        loadSignalWeights();
    }

    // Override initDashboard to also load Phase 2 data
    const _origInit = initDashboard;
    initDashboard = function() {
        if (_origInit) _origInit();
        loadSignalAlerts();
        loadSignalWeights();
        loadAccuracyReport();
        loadAuditSuggestions();
        // Polling for Live System tab data
        setInterval(loadSessionEnergy, 30000);
        setInterval(loadHeartbeat, 60000);
        setInterval(loadCostData, 30000);
    };
    