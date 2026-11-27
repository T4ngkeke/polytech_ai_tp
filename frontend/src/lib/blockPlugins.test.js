import { test } from 'node:test';
import assert from 'node:assert/strict';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkRehype from 'remark-rehype';

import { blockMarkdown, FORMULA_PLUGINS, TABLE_PLUGINS } from './blockPlugins.js';
import { splitMath } from './cellMath.js';

// As react-markdown runs them (it passes allowDangerousHtml to remark-rehype).
async function tree(kind, content) {
  const plugins = kind === 'table' ? TABLE_PLUGINS : FORMULA_PLUGINS;
  const p = unified().use(remarkParse).use(plugins.remark).use(remarkRehype, { allowDangerousHtml: true }).use(plugins.rehype);
  return p.run(p.parse(blockMarkdown(kind, content)));
}

function all(node, out = []) {
  if (node.type === 'element') out.push(node);
  (node.children || []).forEach((n) => all(n, out));
  return out;
}
const text = (node) => (node.type === 'text' ? node.value : (node.children || []).map(text).join(''));
const classes = (el) => [].concat(el.properties?.className || []);

test('a MinerU table is drawn, with its merged cells and decoded quotes', async () => {
  const t = await tree('table', '<table><tr><td colspan="2">Validation</td><td rowspan="2">Test</td></tr>'
    + '<tr><td>l&#x27;erreur</td><td>5.00</td></tr></table>');
  const cells = all(t).filter((el) => el.tagName === 'td');
  assert.equal(cells.length, 4);
  assert.equal(cells[0].properties.colSpan, 2);
  assert.equal(cells[1].properties.rowSpan, 2);
  assert.equal(text(cells[2]), 'l\'erreur');
});

test('scripts, event attributes, styles, classes and links to javascript are removed', async () => {
  const t = await tree('table', '<table><tr><td onclick="alert(1)" style="color:red" class="x">a'
    + '<script>alert(2)</script><img src=x onerror="alert(3)"><a href="javascript:alert(4)">b</a></td></tr></table>'
    + '<iframe src="https://evil"></iframe>');
  const tags = all(t).map((el) => el.tagName);
  assert.ok(!tags.includes('script') && !tags.includes('iframe'));
  for (const el of all(t)) {
    for (const key of Object.keys(el.properties || {})) {
      assert.ok(!/^on|^style$/i.test(key), `${el.tagName}.${key}`);
    }
    assert.ok(!String(el.properties?.href || '').startsWith('javascript:'));
  }
  const cell = all(t).find((el) => el.tagName === 'td');
  assert.deepEqual(classes(cell), []);
  assert.ok(!text(t).includes('alert(2)'), 'the script text is removed too');
});

test('formulas in cells are drawn by KaTeX, other dollars stay text', async () => {
  const t = await tree('table', '<table><tr><td>Test on  $\\mathcal{D}^{3}$ </td><td>$$x^2$$</td><td>5 €</td></tr></table>');
  const katex = all(t).filter((el) => classes(el).includes('katex'));
  assert.equal(katex.length, 2);
  assert.ok(!all(t).some((el) => classes(el).includes('katex-error')));
  assert.equal(splitMath('prix 5 $ et rien'), null);
  assert.equal(splitMath('\\$ 5 \\$'), null);
});

test('a formula block is drawn as a display formula', async () => {
  for (const content of ['$$\n\\mathfrak{C}_{1,m}[k] = \\sum_{p} h[p] \\tag{11}\n$$', '$$a = b$$', 'a = b']) {
    const t = await tree('formula', content);
    assert.ok(all(t).some((el) => classes(el).includes('katex-display')), content);
    assert.ok(!all(t).some((el) => classes(el).includes('katex-error')), content);
  }
});
