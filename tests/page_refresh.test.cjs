const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const StationRegistry = require('../scripts/station_registry.js');

const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
const functionsOnly = inline.slice(0, inline.indexOf("\ndocument.getElementById('collapseBtn')"));
const response = value => ({ok: true, status: 200, json: async () => value});
const unavailable = () => ({ok: false, status: 503});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
const snapshot = (date, time, amount = 5, code = 'TPE500101003') => ({
  time, datetime: `${date}T${time}:00+08:00`, stations: {[code]: [amount, 20]}
});

// Minimal deterministic element doubles: these test application state, not layout.
class Element {
  constructor() {
    this.value = ''; this.options = [{value: ''}]; this.style = {}; this.dataset = {};
    this.listeners = {}; this.textContent = ''; this._html = '';
    const classes = new Set();
    this.classList = {
      toggle(name, force) {
        const include = force === undefined ? !classes.has(name) : force;
        if (include) classes.add(name); else classes.delete(name);
      },
      contains: name => classes.has(name)
    };
  }
  set innerHTML(value) {
    this._html = value;
    if (value.includes('<option')) {
      this.options = [...value.matchAll(/<option value="([^"]*)"/g)].map(m => ({value: m[1]}));
      this.value = this.options[0]?.value || '';
    }
  }
  get innerHTML() { return this._html; }
  appendChild(option) { this.options.push(option); if (option.selected) this.value = option.value; }
  remove(index) { this.options.splice(index, 1); }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  getContext() { return {}; }
  focus() {}
  select() {}
}

function page({fetch = async () => unavailable(), hostname = 'localhost', startup = false} = {}) {
  const elements = new Map(), timers = new Map(), intervals = [], charts = [], requests = [];
  let nextTimer = 1;
  const element = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const context = vm.createContext({
    StationRegistry, AbortController, location: {hostname},
    console: {log() {}},
    document: {getElementById: element, createElement: () => new Element(), querySelectorAll: () => [], querySelector: () => null},
    Chart: class {
      constructor(ctx, config) { this.config = config; charts.push(config); }
      destroy() {}
      resize() {}
    },
    setTimeout(fn) { const id = nextTimer++; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn, ms) { intervals.push({fn, ms}); },
    fetch(url, options) { requests.push({url, options}); return fetch(url, options); }
  });
  vm.runInContext(startup ? inline : functionsOnly, context);
  return {element, timers, intervals, charts, requests, context,
    run: code => vm.runInContext(code, context),
    value: code => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context))};
}

test('page starts with existing controls and a six-minute automatic refresh', async () => {
  const app = page({startup: true});
  await app.run('dataRefreshInFlight');
  assert.equal(app.value('STATIONS.length'), 3387);
  assert.equal(app.value('currentCode'), '500101003');
  assert.ok(app.charts.length > 0);
  assert.equal(app.intervals[0].ms, 360000);
  assert.match(app.element('stats').innerHTML, /3387/);
  assert.equal(app.timers.size, 0);
});

test('production reads raw Git data even when a bot commit does not rebuild Pages', async () => {
  const app = page({hostname: 'hippop3c.github.io'});
  await app.run('loadStationRegistry()');
  assert.match(app.requests[0].url, /^https:\/\/raw\.githubusercontent\.com\/hippop3c\/stationv3\/main\/data\/stations\.json\?t=/);
  assert.equal(app.requests[0].options.cache, 'no-store');
  assert.equal(page().value('DATA_BASE'), 'data');
});

test('new and suspended station metadata updates filters without resetting analysis or dispatches', async () => {
  const rows = [
    {code: '500101003', name: '更新站名 <&>', city: '台北市', district: '大安區', zone: 'ZB3', capacity: 30, status: 2},
    {code: '500298888', name: '測試新站', city: '新北市', district: '測試區', capacity: 25, status: 1}
  ];
  const app = page({fetch: async () => response({schema_version: 1, updated_at: '2026-08-27T23:00:00+08:00', stations: rows})});
  app.run("refreshStationOptions(); currentCode='500101003'; viewMode='live'; getState(currentCode).dispatches[6]=17; getState(currentCode).deltas[7]=99;");
  app.element('cityFilter').value = '台北市';
  app.element('districtFilter').value = '大安區';
  app.element('zoneFilter').value = 'ZB3';
  const oldAnalysis = app.value('stationByCode[currentCode].deltas');
  await app.run('loadStationRegistry()');
  await app.run('loadStationRegistry()');
  assert.equal(app.value('STATIONS.length'), 3388);
  assert.equal(app.value('currentCode'), '500101003');
  assert.equal(app.value('viewMode'), 'live');
  assert.equal(app.value('getState(currentCode).dispatches[6]'), 17);
  assert.equal(app.value('getState(currentCode).deltas[7]'), 99);
  assert.deepEqual(app.value('stationByCode[currentCode].deltas'), oldAnalysis);
  assert.equal(app.value('stationByCode[currentCode].status'), 2);
  assert.equal(app.value("stationByCode['500298888'].zone"), '待設定');
  assert.equal(app.value("stationByCode['500298888'].level"), '無歷史分析');
  assert.equal(app.element('districtFilter').value, '大安區');
  assert.equal(app.element('zoneFilter').value, 'ZB3');
  const cityOptions = app.element('cityFilter').options.map(o => o.value);
  assert.equal(cityOptions.length, new Set(cityOptions).size);
  assert.match(app.element('stationList').innerHTML, /更新站名 &lt;&amp;&gt;/);
  app.run("currentCode='500298888'; viewMode='analysis'; renderDetail()");
  assert.match(app.element('detail').innerHTML, /尚無借還分析/);
  app.run("viewMode='live'; renderDetail()");
  assert.equal(app.charts.at(-1).data.labels.length, 144);
  for (const id of ['cityFilter', 'districtFilter', 'zoneFilter']) app.element(id).value = '';
  app.element('search').value = '測試新站';
  app.element('levelFilter').value = '無歷史分析';
  assert.equal(app.value('getFilteredStations().length'), 1);
});

test('date index refresh preserves the selected historical date', async () => {
  const app = page({fetch: async () => response({'2026-08-26': {count: 2}, '2026-08-27': {count: 3}})});
  app.element('dateSelect').value = '2026-08-26';
  await app.run('loadDateIndex()');
  assert.equal(app.element('dateSelect').value, '2026-08-26');
  assert.deepEqual(app.value('availableDates'), ['2026-08-26', '2026-08-27']);
});

test('late historical responses cannot override a newer date selection', async () => {
  const firstBody = deferred();
  const app = page({fetch: async url => url.includes('2026-08-26')
    ? {ok: true, json: () => firstBody.promise}
    : response([snapshot('2026-08-27', '08:00')])});
  const earlier = app.run("loadReplay('2026-08-26')");
  await app.run("loadReplay('2026-08-27')");
  firstBody.resolve([snapshot('2026-08-26', '06:00')]);
  await earlier;
  assert.equal(app.value('replayData.date'), '2026-08-27');
  assert.equal(app.value('selectedDate'), '2026-08-27');
  assert.equal(app.timers.size, 0);
});

test('latest records refresh the live curve and follow it across midnight', async () => {
  let date = '2026-08-27', time = '23:59';
  const app = page({fetch: async url => response(url.includes('index.json')
    ? {'2026-08-27': {count: 1}, ...(date === '2026-08-28' ? {'2026-08-28': {count: 1}} : {})}
    : [snapshot(date, time)])});
  app.run("setViewMode('live')"); // Also works before the date index arrives.
  await app.run('loadLatest()');
  assert.equal(app.value('replayData.records[0].time'), '23:59');
  date = '2026-08-28'; time = '00:01';
  await app.run('loadLatest()');
  assert.equal(app.value('replayData.date'), date);
  assert.equal(app.value('liveData.time'), time);
  assert.equal(app.element('dateSelect').value, date);
});

test('automatic refresh does not jump away from a manually selected old day', async () => {
  const app = page({fetch: async url => response(url.includes('index.json')
    ? {'2026-08-26': {count: 1}, '2026-08-27': {count: 1}}
    : [snapshot(url.includes('2026-08-26') ? '2026-08-26' : '2026-08-27', '12:00')])});
  app.element('dateSelect').value = '2026-08-26';
  await app.run("loadReplay('2026-08-26')");
  await app.run('loadLatest()');
  assert.equal(app.value('replayData.date'), '2026-08-26');
  assert.equal(app.value('selectedDate'), '2026-08-26');
  assert.equal(app.element('dateSelect').value, '2026-08-26');
  assert.match(app.value('liveData.datetime'), /^2026-08-27/);
});

test('refresh requests coalesce, then retry after a timed-out registry request', async () => {
  const app = page({fetch: (url, options) => {
    if (!url.includes('stations.json')) return Promise.resolve(response({}));
    return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('timed out')), {once: true}));
  }});
  const first = app.run('refreshData()');
  const second = app.run('refreshData()');
  assert.equal(first, second);
  assert.equal(app.requests.length, 2);
  // Fire the bounded request timer rather than sleeping for 45 seconds.
  for (const fire of [...app.timers.values()]) fire();
  await first;
  assert.equal(app.value('dataRefreshInFlight'), null);
  const retry = app.run('refreshData()');
  assert.equal(app.requests.length, 4);
  for (const fire of [...app.timers.values()]) fire();
  await retry;
  assert.equal(app.value('STATIONS.length'), 3387);
});

test('live chart keeps 23:59, actual zeros, missing values and latest values in each bucket', () => {
  const app = page();
  app.context.testRecords = [
    snapshot('2026-08-27', '23:59', 9), snapshot('2026-08-27', '23:55', 5),
    {...snapshot('2026-08-27', '00:00'), stations: {TPE500101003: [0, 0]}},
    {...snapshot('2026-08-27', '01:00'), stations: {TPE500101003: [null, null]}}
  ];
  app.run("replayData={date:'2026-08-27',records:testRecords}; drawLiveChart(stationByCode['500101003'])");
  const data = app.charts.at(-1).data;
  assert.equal(data.labels.length, 144);
  assert.equal(data.datasets[0].data[143], 9);
  assert.equal(data.datasets[0].data[0], 0);
  assert.equal(data.datasets[1].data[0], 0);
  assert.equal(data.datasets[0].data[6], null);
  assert.equal(data.datasets[1].data[6], null);
});
