"""Edit the dashboard template to add Session Energy section to Live System tab."""
import re

with open('templates/suite_dashboard.html', 'r', encoding='utf-8') as f:
    content = f.read()

# ============================================================
# 1. Add Session Energy section after Macro Engine in Live System tab
# ============================================================

old_live_tab_end = """            </div>
        </div>

        <!-- ── TAB: PARAMETERS ──────────────────────────── -->"""

new_live_tab_end = """            </div>

            <!-- ── SESSION ENERGY (from session lock packet_data) ── -->
            <div class="table-section">
                <div class="table-header" style="display:flex; justify-content:space-between; align-items:center;">
                    <span>Session Energy</span>
                    <span id="energySessionLabel" style="font-family:'JetBrains Mono',monospace; font-size:10px; color:#6b5345;">—</span>
                </div>
                <div class="chart-row row-1-1" style="margin-bottom:18px;">
                    <!-- Fuel Gauge: 15M -->
                    <div class="chart-card">
                        <div class="chart-title">15M JEWEL</div>
                        <div class="chart-sub" id="fuel15mStatus">Loading...</div>
                        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:8px;">
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Trend</span><br><span id="fuel15mTrend" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Momentum</span><br><span id="fuel15mMomentum" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">ADX</span><br><span id="fuel15mAdx" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">RSI</span><br><span id="fuel15mRsi" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Ribbon Spread</span><br><span id="fuel15mSpread" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Deviation</span><br><span id="fuel15mDev" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                        </div>
                        <div id="fuel15mExit" style="margin-top:8px; font-family:'JetBrains Mono'; font-size:10px; color:#8c4a3a; display:none;"></div>
                    </div>
                    <!-- Fuel Gauge: 1H -->
                    <div class="chart-card">
                        <div class="chart-title">1H</div>
                        <div class="chart-sub" id="fuel1hStatus">Loading...</div>
                        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:8px;">
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Trend</span><br><span id="fuel1hTrend" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Momentum</span><br><span id="fuel1hMomentum" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">ADX</span><br><span id="fuel1hAdx" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">RSI</span><br><span id="fuel1hRsi" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Ribbon Spread</span><br><span id="fuel1hSpread" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Deviation</span><br><span id="fuel1hDev" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                        </div>
                        <div id="fuel1hExit" style="margin-top:8px; font-family:'JetBrains Mono'; font-size:10px; color:#8c4a3a; display:none;"></div>
                    </div>
                </div>
                <div class="chart-row row-1-1" style="margin-bottom:18px;">
                    <!-- Fuel Gauge: 4H -->
                    <div class="chart-card">
                        <div class="chart-title">4H</div>
                        <div class="chart-sub" id="fuel4hStatus">Loading...</div>
                        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:8px;">
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Trend</span><br><span id="fuel4hTrend" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Momentum</span><br><span id="fuel4hMomentum" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">ADX</span><br><span id="fuel4hAdx" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">RSI</span><br><span id="fuel4hRsi" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Ribbon Spread</span><br><span id="fuel4hSpread" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Deviation</span><br><span id="fuel4hDev" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                        </div>
                        <div id="fuel4hExit" style="margin-top:8px; font-family:'JetBrains Mono'; font-size:10px; color:#8c4a3a; display:none;"></div>
                    </div>
                    <!-- Bias Model + Levels -->
                    <div class="chart-card">
                        <div class="chart-title">Bias &amp; Levels</div>
                        <div class="chart-sub" id="energyBiasSub">Loading...</div>
                        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:8px;">
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Daily Lean</span><br><span id="energyDailyLean" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Permission</span><br><span id="energyPermission" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Macro Bias</span><br><span id="energyMacroBias" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Micro Bias</span><br><span id="energyMicroBias" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Micro State</span><br><span id="energyMicroState" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                            <div><span style="color:#8a6f57; font-size:9px; text-transform:uppercase; letter-spacing:1px;">Jewel Gate</span><br><span id="energyJewelGate" style="font-family:'JetBrains Mono'; font-size:16px; font-weight:800; color:#f2e3cf;">—</span></div>
                        </div>
                        <div style="margin-top:12px; padding-top:12px; border-top:1px solid #4d3526;">
                            <div style="font-family:'JetBrains Mono'; font-size:9px; color:#8a6f57; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">Key Levels</div>
                            <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:6px; font-family:'JetBrains Mono'; font-size:11px;">
                                <div><span style="color:#6b5345;">BO:</span> <span id="energyBo" style="color:#5e8c66;">—</span></div>
                                <div><span style="color:#6b5345;">BD:</span> <span id="energyBd" style="color:#8c4a3a;">—</span></div>
                                <div><span style="color:#6b5345;">POC:</span> <span id="energyPoc" style="color:#c9a24a;">—</span></div>
                                <div><span style="color:#6b5345;">R30H:</span> <span id="energyR30h" style="color:#f2e3cf;">—</span></div>
                                <div><span style="color:#6b5345;">R30L:</span> <span id="energyR30l" style="color:#f2e3cf;">—</span></div>
                                <div><span style="color:#6b5345;">ATR:</span> <span id="energyAtr" style="color:#f2e3cf;">—</span></div>
                                <div><span style="color:#6b5345;">EMA20:</span> <span id="energyEma20" style="color:#94a3b8;">—</span></div>
                                <div><span style="color:#6b5345;">EMA30:</span> <span id="energyEma30" style="color:#94a3b8;">—</span></div>
                                <div><span style="color:#6b5345;">EMA50:</span> <span id="energyEma50" style="color:#94a3b8;">—</span></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- ── SYSTEM HEALTH (heartbeat from admin page) ── -->
            <div class="table-section">
                <div class="table-header">System Health</div>
                <div class="table-wrap">
                    <div style="padding: 16px 20px; display: flex; gap: 32px; flex-wrap: wrap;">
                        <div>
                            <div style="font-family:'JetBrains Mono'; font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">session_audit_log</div>
                            <div style="display:flex; align-items:center; gap:8px;">
                                <span id="hbAuditDot" style="width:10px; height:10px; border-radius:50%; background:#334155; display:inline-block;"></span>
                                <span id="hbAuditStatus" style="font-family:'JetBrains Mono'; font-size:13px; font-weight:800; color:#64748b;">—</span>
                            </div>
                            <div id="hbAuditDetail" style="font-family:'JetBrains Mono'; font-size:10px; color:#64748b; margin-top:4px;">loading...</div>
                        </div>
                        <div>
                            <div style="font-family:'JetBrains Mono'; font-size:10px; color:#64748b; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">monitor_event_log</div>
                            <div style="display:flex; align-items:center; gap:8px;">
                                <span id="hbMonDot" style="width:10px; height:10px; border-radius:50%; background:#334155; display:inline-block;"></span>
                                <span id="hbMonStatus" style="font-family:'JetBrains Mono'; font-size:13px; font-weight:800; color:#64748b;">—</span>
                            </div>
                            <div id="hbMonDetail" style="font-family:'JetBrains Mono'; font-size:10px; color:#64748b; margin-top:4px;">loading...</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- ── TAB: PARAMETERS ──────────────────────────── -->"""

if old_live_tab_end not in content:
    print("ERROR: Could not find Live System tab end marker!")
    print("Trying to find it...")
    idx = content.find('TAB: PARAMETERS')
    print(f"Found 'TAB: PARAMETERS' at position {idx}")
    print(repr(content[idx-200:idx+50]))
else:
    content = content.replace(old_live_tab_end, new_live_tab_end, 1)
    print("1. Added Session Energy + System Health sections to Live System tab")

# ============================================================
# 2. Add loadSessionEnergy() and loadHeartbeat() JS functions
# ============================================================

# Find the exact text after loadLiveSystem function
idx_live = content.find('loadLiveSystem')
idx_params = content.find('async function loadParameters', idx_live)

old_after_live = content[idx_params:idx_params+50]
print(f"2. Found marker: {repr(old_after_live[:40])}")

new_js = """    }

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
            document.getElementById('energyDailyLean').textContent = bm.daily_lean || '-';
            document.getElementById('energyPermission').textContent = bm.permission_state || '-';
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

    async function loadParameters() {"""

content = content.replace(old_after_live, new_js, 1)
print("3. Added loadSessionEnergy() and loadHeartbeat() JS functions")

# ============================================================
# 3. Add calls to loadSessionEnergy() and loadHeartbeat() in the tab switch
# ============================================================

old_tab_switch = """            if (tab === 'live-system') {
                loadLiveSystem();
            }"""

new_tab_switch = """            if (tab === 'live-system') {
                loadLiveSystem();
                loadSessionEnergy();
                loadHeartbeat();
            }"""

if old_tab_switch not in content:
    print("ERROR: Could not find tab switch call!")
else:
    content = content.replace(old_tab_switch, new_tab_switch, 1)
    print("4. Added loadSessionEnergy() and loadHeartbeat() to tab switch")

# ============================================================
# 4. Add heartbeat interval (same as admin page: 60s)
# ============================================================

old_interval = """        setInterval(loadLiveSystem, 30000);"""

new_interval = """        setInterval(loadLiveSystem, 30000);
        setInterval(loadHeartbeat, 60000);"""

if old_interval not in content:
    print("ERROR: Could not find loadLiveSystem interval!")
else:
    content = content.replace(old_interval, new_interval, 1)
    print("5. Added heartbeat interval")

# ============================================================
# Write the modified file
# ============================================================
with open('templates/suite_dashboard.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done! Dashboard updated.")
