// Ask ZEO 1.71.0 (A4): export the frontend menus (shared-ui/menu-config.ts) to AskZeo/data/menu.json for the how-to help.
// Run after a menu change:  node zeo-hrms/AskZeo/tools/export_menu.js <path to zeo-frontend/hrms-project>
// Needs the frontend's node_modules (typescript). The JSON copy in the package is used when this is not run.
const fs = require('fs'), path = require('path'), vm = require('vm');
const fe = path.resolve(process.argv[2] || path.join(__dirname, '../../../zeo-frontend/hrms-project'));
const ts = require(path.join(fe, 'node_modules/typescript'));
const dir = path.join(fe, 'src/app/shared-ui');
const mods = {};
function load(name) {
  if (mods[name]) return mods[name].exports;
  const src = fs.readFileSync(path.join(dir, name + '.ts'), 'utf8');
  const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const m = { exports: {} }; mods[name] = m;
  vm.runInNewContext(js, { module: m, exports: m.exports, require: (p) => load(p.replace('./', '')), console });
  return m.exports;
}
const mc = load('menu-config');
const out = { suites: mc.SUITES, groups: mc.MAIN_GROUPS.map(g => ({ title: g.title, suite: g.suite || '', apps: g.apps.map(a => ({
  key: a.key, label: a.label, link: a.link || '', perms: a.perms || [], country: a.country || '',
  sections: (a.sections || []).map(s => ({ title: s.title, items: s.items.map(i => ({ label: i.label, link: i.link, hint: i.hint || '', perms: i.perms || [], country: i.country || '' })) })) })) })) };
const dest = path.join(__dirname, '../data/menu.json');
fs.writeFileSync(dest, JSON.stringify(out, null, 1));
const n = out.groups.reduce((a, g) => a + g.apps.reduce((b, x) => b + x.sections.reduce((c, s) => c + s.items.length, 0) + (x.link ? 1 : 0), 0), 0);
console.log('menu.json:', out.groups.length, 'groups,', n, 'screens');
