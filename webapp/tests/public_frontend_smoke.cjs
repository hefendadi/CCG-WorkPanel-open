const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const read = p => fs.readFileSync(path.join(root,p),'utf8');
const vm = require('node:vm');

test('login targets Portal and shared shell exposes public modules', () => {
  assert.match(read('webapp/static/js/login.js'), /location.href\s*=\s*['"]\/['"]/);
  const shell=read('webapp/static/ccg-design-system/shell.js');
  for(const route of ['/mdm','/sales/actual','/ordering']) assert.ok(shell.includes(route));
  assert.ok(!shell.includes('/workbench'));
});
test('all linked static assets exist', () => {
  for(const file of fs.readdirSync(path.join(root,'webapp/static')).filter(f=>f.endsWith('.html'))) {
    const html=read('webapp/static/'+file);
    for(const m of html.matchAll(/(?:src|href)=["'](\/static\/[^"']+)["']/g))
      assert.ok(fs.existsSync(path.join(root,'webapp',m[1].split('?')[0])),m[1]);
  }
});
test('fixed-point projection preserves blanks and zero', () => {
  const context={}; vm.runInNewContext(read('webapp/static/js/ordering-calculation.js'),context);
  const c=context.OrderingCalculation;
  const row={opening:'41',incoming:['13','0','0','0'],channels:[{forecast:['5','6','7','8']}]};
  assert.deepEqual(Array.from(c.calculate(row,true).projected),['49','43','36','28']);
  row.channels[0].forecast[0]=null;
  assert.equal(c.calculate(row,true).projected[0],null);
  assert.equal(c.units('0'),0n);assert.equal(c.units(''),null);
});
test('decimal formatting does not round through Number', () => {
  const context={};vm.runInNewContext(read('webapp/static/js/sales-dashboard-format.js'),context);
  assert.equal(context.salesDashboardFormat.qty('12345678901234.0001'),'12,345,678,901,234.0001');
});
