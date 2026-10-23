// The shared admin initializer must work with only the formal config present.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname,
  '_templates/otree/includes/DynamicSessionControls.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
const setup = script.slice(0, script.indexOf("  let currentConfig = '';"))
  + '\ninitialized = true;\n});';

for (const configs of [
  ['dynamic_bottleneck_round_prod'],
  ['single_bottleneck_prod_agent_active', 'single_bottleneck_demo_agent_active',
   'dynamic_bottleneck_round_prod', 'dynamic_bottleneck_round_demo'],
]) {
  const node = () => ({value: '', closest() { return this; }, setAttribute() {}});
  const dropdown = {options: configs.map(value => ({value}))};
  const document = {
    addEventListener(event, callback) { callback(); },
    getElementById() { return node(); },
    querySelector(selector) {
      if (selector === '[name="session_config"]') return dropdown;
      if (selector === '[name="num_participants"]') return node();
      if (selector.includes('instance_information_condition')) return {value: 'I1'};
      if (configs.some(config => selector.startsWith(`[name="${config}.`))) return node();
      return null;
    },
  };
  const context = {document, initialized: false};
  vm.runInNewContext(setup, context);
  assert.equal(context.initialized, true, `initializer stopped with ${configs.length} config(s)`);
}
console.log('Admin initialization: isolated formal config and legacy form passed');
