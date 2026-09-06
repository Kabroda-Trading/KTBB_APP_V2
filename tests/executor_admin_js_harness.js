// Node-based smoke harness for templates/executor_admin.html's inline
// <script> block. Not a browser -- no DOM rendering, no real network --
// but it genuinely EXECUTES the actual page JavaScript with a minimal
// document/fetch/alert mock, which is more than static review alone
// caught: a real incident (2026-09-05) shipped four onclick="" handlers
// with one fewer argument than the functions they called (the click-
// guard helper needed the button element, the handlers were never
// updated to pass `this`), and every click threw a TypeError before
// ever calling fetch() -- silent in the browser, invisible in server
// logs (no request was ever sent), and completely outside what any
// Python-side test could have caught.
//
// CRITICAL DESIGN POINT, itself the product of a second near-miss while
// building this harness: this must execute the LITERAL onclick="..."
// attribute text out of the rendered HTML template's own account-card
// markup, bound to a real element as `this` -- exactly how a browser
// invokes an inline handler. An earlier draft of this file instead
// hand-wrote its own belief about what arguments each function "should"
// receive and called the function directly -- which cannot detect the
// exact bug this harness exists to catch, since the function and the
// onclick string can drift independently and a hand-written call only
// tests one side of that drift.
//
// Run directly: node tests/executor_admin_js_harness.js
// Wrapped by tests/test_executor_admin_js.py (skips cleanly if Node
// isn't installed -- this repo has no other Node dependency).

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const TEMPLATE_PATH = path.join(__dirname, '..', 'templates', 'executor_admin.html');

function extractScript(html) {
  const match = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!match) throw new Error('No <script> block found in ' + TEMPLATE_PATH);
  return match[1];
}

// Finds the literal onclick="..." attribute text for a button whose
// onclick calls `fnName(...)`, by rendering the account-card template
// literal with known stand-in values, then locating that call. We
// don't have a real Jinja2/JS template engine here, so instead we
// substitute the ${...} placeholders this template's own JS uses for
// account/test ids with fixed sentinel values via simple string
// replacement -- close enough to get a real, executable onclick string
// for these specific buttons (all of which take only accountId and/or
// active.id, never full HTML-escaped content).
function findOnclickAttr(html, fnName) {
  const re = new RegExp(`onclick="(${fnName}\\([^)]*\\))"`, 'g');
  const found = [];
  let m;
  while ((m = re.exec(html)) !== null) found.push(m[1]);
  if (found.length === 0) throw new Error(`No onclick="${fnName}(...)" attribute found in the template`);
  if (found.length > 1) throw new Error(`Found ${found.length} onclick="${fnName}(...)" attributes -- expected exactly one, update this harness`);
  return found[0]
    .replace(/\$\{accountId\}/g, '1')
    .replace(/\$\{active\.id\}/g, '5');
}

function buildSandbox(fetchCalls) {
  const elements = new Map();
  function makeEl(id) {
    if (!elements.has(id)) elements.set(id, { id, value: '', textContent: '', disabled: false, innerHTML: '', style: {} });
    return elements.get(id);
  }
  const sandbox = {
    console,
    document: { getElementById: (id) => makeEl(id) },
    fetch: async (requestPath, opts) => {
      fetchCalls.push({ path: requestPath, opts });
      let body = { ok: true };
      if (/\/tiny-test\/(place|\d+\/(partial-close|move-sl-breakeven|flash-close|place-resting-t1-limit|check-resting-t1-limit-status|cancel-resting-t1-limit))$/.test(requestPath)) {
        body = { ok: true, test: { id: 1, status: 'TPSL_SET' } };
      } else if (/\/tiny-test$/.test(requestPath)) {
        body = { ok: true, tests: [] };
      } else if (/\/accounts$/.test(requestPath)) {
        body = { ok: true, accounts: [] };
      } else if (/\/risk-state$/.test(requestPath)) {
        body = { ok: true, risk_state: {} };
      } else if (/\/global-config$/.test(requestPath)) {
        body = { ok: true, global_kill_switch_engaged: false, live_orders_enabled: true };
      } else if (/\/orders/.test(requestPath)) {
        body = { ok: true, orders: [] };
      } else if (/\/audit-log/.test(requestPath)) {
        body = { ok: true, audit_log: [] };
      }
      return { json: async () => body };
    },
    alert: () => {},
    Object, JSON, Date, parseFloat, isNaN,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, makeEl };
}

async function main() {
  const html = fs.readFileSync(TEMPLATE_PATH, 'utf-8');
  const script = extractScript(html);

  const fetchCalls = [];
  const { sandbox, makeEl } = buildSandbox(fetchCalls);
  vm.createContext(sandbox);

  try {
    vm.runInContext(script, sandbox, { filename: 'executor_admin.html:script' });
  } catch (e) {
    console.log(JSON.stringify([{ label: 'top-level script execution', ok: false, error: e.message }]));
    process.exit(1);
  }

  // Let the fire-and-forget top-level loadGlobalConfig()/loadAccounts()/
  // loadOrders()/loadAuditLog() calls settle before clicking anything.
  await new Promise((r) => setTimeout(r, 50));

  // _parseServerTimestamp() unit check (2026-09-06, real bug caught
  // live by Andy) -- direct function-level assertions, not just "the
  // page didn't throw." Naive-UTC strings (no zone marker, exactly what
  // Python's .isoformat() on a datetime.utcnow()-based column produces)
  // must get "Z" appended; already-tagged strings must be left alone.
  const timestampResults = [];
  try {
    const fn = sandbox._parseServerTimestamp;
    const cases = [
      { input: '2026-09-06T18:18:07.123456', expected: '2026-09-06T18:18:07.123Z', label: 'naive UTC string gets Z appended' },
      { input: '2026-09-06T18:18:07Z', expected: '2026-09-06T18:18:07.000Z', label: 'already-Z string not double-appended' },
      { input: '2026-09-06T18:18:07+00:00', expected: '2026-09-06T18:18:07.000Z', label: 'explicit-offset string not double-appended' },
    ];
    for (const c of cases) {
      const actual = fn(c.input).toISOString();
      timestampResults.push({
        label: `_parseServerTimestamp: ${c.label}`, ok: actual === c.expected,
        error: actual === c.expected ? undefined : `expected ${c.expected}, got ${actual}`,
      });
    }
    timestampResults.push({ label: '_parseServerTimestamp: null input', ok: fn(null) === null });
  } catch (e) {
    timestampResults.push({ label: '_parseServerTimestamp', ok: false, error: e.message });
  }

  const SCENARIOS = [
    { label: 'placeTinyTest', expectPath: /\/tiny-test\/place$/ },
    { label: 'partialCloseTinyTest', expectPath: /\/tiny-test\/5\/partial-close$/ },
    { label: 'moveSlBreakevenTinyTest', expectPath: /\/tiny-test\/5\/move-sl-breakeven$/ },
    { label: 'flashCloseTinyTest', expectPath: /\/tiny-test\/5\/flash-close$/ },
    // 2026-09-06, ladder-test completion build -- same bug class this
    // harness exists to catch, new surface area of it.
    { label: 'placeRestingT1Limit', expectPath: /\/tiny-test\/5\/place-resting-t1-limit$/ },
    { label: 'checkRestingT1LimitStatus', expectPath: /\/tiny-test\/5\/check-resting-t1-limit-status$/ },
    { label: 'cancelRestingT1Limit', expectPath: /\/tiny-test\/5\/cancel-resting-t1-limit$/ },
  ];

  const results = [];
  for (const scenario of SCENARIOS) {
    fetchCalls.length = 0;
    let onclickAttr;
    try {
      onclickAttr = findOnclickAttr(html, scenario.label);
    } catch (e) {
      results.push({ label: scenario.label, ok: false, error: e.message });
      continue;
    }

    const btn = makeEl(`__fake_btn_${scenario.label}__`);
    // A real inline onclick="..." handler doesn't return its promise to
    // the browser either (an async function's rejection there becomes
    // an unhandled rejection logged to the console, not a thrown error
    // anyone catches) -- so this harness must catch it the same way,
    // via the process-level event, rather than a try/catch around the
    // call (which would NOT see a rejection from a fire-and-forget
    // promise the handler itself never awaited or returned).
    let capturedRejection = null;
    const onRejection = (reason) => { capturedRejection = reason; };
    process.on('unhandledRejection', onRejection);
    try {
      // Compiled INSIDE the same vm context (not a bare `new Function`,
      // which would run in Node's own global scope and never see
      // placeTinyTest() etc. at all).
      const handler = vm.runInContext(`(function () { ${onclickAttr} })`, sandbox);
      handler.call(btn);
      // Give the fire-and-forget async call a tick to run to completion
      // (or throw) before checking what happened -- same reasoning a
      // browser's own event loop would apply.
      await new Promise((r) => setTimeout(r, 20));

      if (capturedRejection) {
        throw capturedRejection instanceof Error ? capturedRejection : new Error(String(capturedRejection));
      }
      const matched = fetchCalls.some((c) => scenario.expectPath.test(c.path));
      results.push({
        label: scenario.label, ok: matched, onclickAttr,
        error: matched ? undefined : `expected a fetch matching ${scenario.expectPath}, got: ${fetchCalls.map(c => c.path).join(', ') || '(none)'}`,
        fetchCalls: fetchCalls.map((c) => c.path),
      });
    } catch (e) {
      results.push({ label: scenario.label, ok: false, onclickAttr, error: e.message });
    } finally {
      process.off('unhandledRejection', onRejection);
    }
  }

  const allResults = timestampResults.concat(results);
  console.log(JSON.stringify(allResults, null, 2));
  process.exit(allResults.some((r) => !r.ok) ? 1 : 0);
}

main();
