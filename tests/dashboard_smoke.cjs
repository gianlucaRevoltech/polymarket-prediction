// Compile and execute dashboard JS with a minimal DOM, without network/services.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../src/templates/index.html'), 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).filter(s => s.trim());
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    style: {}, dataset: {}, innerHTML: '', textContent: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, getContext() { return { createLinearGradient() { return { addColorStop() {} }; } }; },
    querySelectorAll() { return []; }, setAttribute() {}, getAttribute() { return null; },
  });
  return elements.get(id);
};
const document = {
  getElementById: element, documentElement: element('root'), body: element('body'),
  querySelectorAll() { return []; }, addEventListener() {},
};
const context = vm.createContext({
  document, localStorage: { getItem() { return null; }, setItem() {} },
  window: { matchMedia() { return { matches: false }; } },
  getComputedStyle() { return { getPropertyValue() { return '#123456'; } }; },
  Chart: class { constructor(_, opts) { this.data = opts.data; this.options = opts.options; } update() {} destroy() {} },
  setInterval() {}, setTimeout() {}, clearInterval() {},
  fetch() { return new Promise(() => {}); }, console,
});
scripts.forEach(js => new vm.Script(js).runInContext(context));
const summary = { execution_mode: 'paper_validation', run_id: 'smoke', peak_equity: 300 };
const economic = { net_pnl: -.524629, fees_usdc: null, max_positive_wallet_share: null, max_positive_domain_share: null };
context.updateRiskDashboard(summary, {}, {}, {}, { ready: true, activation: { status: 'pending' } }, economic, {}, 'paper_validation');
assert.match(element('riskContainer').innerHTML, /n\/d \/ n\/d/);
assert.match(element('riskContainer').innerHTML, /pending/);
assert.match(element('riskContainer').innerHTML, /EDGE NON DIMOSTRATO/);
assert.match(element('riskContainer').innerHTML, /-\$0\.52/);
assert.match(element('riskContainer').innerHTML, /fee n\/d/);
console.log('Dashboard JS smoke: PASS');
