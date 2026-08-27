/* Metadata-only, append-only merge. CPS analysis and user simulation state
 * remain separate from station discovery and TDX observation snapshots. */
const StationRegistry = (() => {
  const fields = ['name', 'city', 'district', 'zone', 'capacity', 'status',
    'first_seen_at', 'last_seen_at', 'source_present'];

  function metadata(row) {
    if (!row || typeof row !== 'object') return null;
    const code = String(row.code || '').trim();
    if (!/^500[12]\d{5}$/.test(code) || /^500[12]99/.test(code)) return null;
    const capacity = row.capacity;
    if (!Number.isInteger(capacity) || capacity < 0) return null;
    if (!['name', 'city', 'district'].every(k => typeof row[k] === 'string' && row[k].trim())) return null;
    const value = {code, capacity};
    for (const k of ['name', 'city', 'district']) value[k] = row[k].trim();
    if (typeof row.zone === 'string' && !['', 'nan', '待設定', '待確認'].includes(row.zone.trim())) value.zone = row.zone.trim();
    for (const k of ['first_seen_at', 'last_seen_at']) {
      if (typeof row[k] === 'string') value[k] = row[k];
    }
    if (row.status != null && [0, 1, 2].includes(Number(row.status))) value.status = Number(row.status);
    if (typeof row.source_present === 'boolean') value.source_present = row.source_present;
    return value;
  }

  function merge(stations, rows) {
    if (!Array.isArray(rows)) throw new Error('Invalid station registry');
    const valid = rows.map(metadata).filter(Boolean);
    if (!valid.length) throw new Error('Empty station registry');
    if (new Set(valid.map(s => s.code)).size !== valid.length) throw new Error('Duplicate station code');
    const byCode = new Map(stations.map(s => [s.code, s]));
    let added = 0, changed = false;
    for (const incoming of valid) {
      let station = byCode.get(incoming.code);
      if (!station) {
        station = {code: incoming.code, zone: '待設定', avgMaxBind: 0,
          A: null, B: null, level: '無歷史分析', total_viol: null,
          deltas: Array(24).fill(0), disp: Array(24).fill(0)};
        stations.push(station);
        byCode.set(station.code, station);
        added += 1;
        changed = true;
      }
      for (const field of fields) {
        if (field === 'district' && incoming[field] === '待確認' && station.district && station.district !== '待確認') continue;
        if (incoming[field] !== undefined && station[field] !== incoming[field]) {
          station[field] = incoming[field];
          changed = true;
        }
      }
    }
    return {added, changed, total: stations.length};
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[ch]);
  }

  function observation(values) {
    const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
    const rent = number(Array.isArray(values) ? values[0] : null);
    const availableReturn = number(Array.isArray(values) ? values[1] : null);
    return {rent, availableReturn, capacity: rent === null || availableReturn === null ? null : rent + availableReturn};
  }

  function timeBucket(time) {
    const match = /^(\d{2}):(\d{2})$/.exec(time || '');
    if (!match) return -1;
    const hour = Number(match[1]), minute = Number(match[2]);
    return hour < 24 && minute < 60 ? hour * 6 + Math.floor(minute / 10) : -1;
  }

  return {merge, escapeHtml, observation, timeBucket};
})();

if (typeof module !== 'undefined' && module.exports) module.exports = StationRegistry;
