const test = require('node:test');
const assert = require('node:assert/strict');
const registry = require('../scripts/station_registry.js');

function base() {
  return {code:'500101003', name:'原站', city:'台北市', district:'大安區', zone:'ZB3', capacity:28,
    A:7, B:2, level:'最優解', avgMaxBind:2, total_viol:0, deltas:Array(24).fill(3), disp:Array(24).fill(-2)};
}

test('rename, capacity, suspended and missing metadata never erase history or objects', () => {
  const original = base(), absent = {...base(), code:'500101004'};
  const history = structuredClone(original);
  const rows = [original, absent];
  registry.merge(rows, [{...base(), name:'改名', capacity:32, status:2, A:999, deltas:[]}]);
  assert.equal(rows[0], original);
  assert.equal(rows[1], absent);
  assert.equal(original.status, 2);
  assert.equal(original.name, '改名');
  for (const key of ['A','B','avgMaxBind','level','total_viol','deltas','disp']) assert.deepEqual(original[key], history[key]);
});

test('new stations appear once with honest analysis defaults and independent arrays', () => {
  const rows = [base()];
  const a = {...base(), code:'500101998', zone:undefined}, b = {...base(), code:'500201998'};
  assert.equal(registry.merge(rows, [a,b]).added, 2);
  assert.equal(registry.merge(rows, [a,b]).added, 0);
  assert.equal(rows.length, 3);
  assert.equal(rows[1].zone, '待設定');
  assert.equal(rows[1].A, null);
  assert.equal(rows[1].B, null);
  assert.equal(rows[1].level, '無歷史分析');
  assert.equal(rows[1].deltas.length, 24);
  assert.equal(rows[1].disp.length, 24);
  rows[1].deltas[0] = 5;
  assert.equal(rows[2].deltas[0], 0);
});

test('invalid or duplicate registry cannot mutate the fallback roster', () => {
  const rows = [base()], before = structuredClone(rows);
  for (const input of [[], null, [{...base(), code:'bad'}], [base(), base()]]) {
    assert.throws(() => registry.merge(rows, input));
    assert.deepEqual(rows, before);
  }
});

test('unknown zone cannot erase a CPS responsibility zone', () => {
  const rows = [base()];
  for (const zone of ['', 'nan', '待設定', '待確認']) {
    registry.merge(rows, [{...base(), zone, district:'待確認'}]);
    assert.equal(rows[0].zone, 'ZB3');
    assert.equal(rows[0].district, base().district);
  }
});

test('metadata text is escaped before HTML rendering', () => {
  assert.equal(registry.escapeHtml('<img x="a">&\''), '&lt;img x=&quot;a&quot;&gt;&amp;&#39;');
});

test('missing observations are null, while actual zero remains zero', () => {
  assert.deepEqual(registry.observation([null,null]), {rent:null, availableReturn:null, capacity:null});
  assert.deepEqual(registry.observation([3,null]), {rent:3, availableReturn:null, capacity:null});
  assert.deepEqual(registry.observation([0,0]), {rent:0, availableReturn:0, capacity:0});
  assert.equal(registry.observation(['3',7]).capacity, null);
});

test('last minutes before midnight remain in the final history bucket', () => {
  assert.equal(registry.timeBucket('23:54'), 143);
  assert.equal(registry.timeBucket('23:55'), 143);
  assert.equal(registry.timeBucket('23:59'), 143);
  assert.equal(registry.timeBucket('00:00'), 0);
  assert.equal(registry.timeBucket('07:59'), 47);
  assert.equal(registry.timeBucket('24:00'), -1);
});
