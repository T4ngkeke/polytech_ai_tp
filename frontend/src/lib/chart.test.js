import { test } from 'node:test';
import assert from 'node:assert/strict';

import { niceTicks, parseChart } from './chart.js';

test('a chart in the checked shape is read', () => {
  const c = parseChart('{"type":"bar","title":"T","labels":["10","100"],"series":[{"name":"a","values":[1,null]}]}');
  assert.equal(c.type, 'bar');
  assert.deepEqual(c.series[0].values, [1, null]);
});

test('anything else is refused, as on the server', () => {
  for (const wrong of [
    'not json', '[]', '{"type":"html","labels":["a"],"series":[{"values":[1]}]}',
    '{"type":"bar","labels":["a","b"],"series":[{"values":[1]}]}',
    '{"type":"pie","labels":["a"],"series":[{"values":[-1]}]}',
    '{"type":"scatter","labels":["x"],"series":[{"values":[1]}]}',
    '{"type":"bar","labels":["a"],"series":[{"values":[1]}],"onclick":"x"}',
  ]) assert.equal(parseChart(wrong), null, wrong);
});

test('ticks are round numbers around the values', () => {
  const [lo, hi, ticks] = niceTicks(0, 500000);
  assert.equal(lo, 0);
  assert.ok(hi >= 500000);
  assert.ok(ticks.length >= 4 && ticks.length <= 7);
  assert.deepEqual(niceTicks(3, 3).slice(0, 2), [2, 4]);
});
