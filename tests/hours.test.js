// Run with: node tests/hours.test.js
const assert = require('assert');
const { isOpenAt } = require('../static/hours.js');

// Wednesday 2026-11-04 at the given local time.
const wed = (h, m = 0) => new Date(2026, 10, 4, h, m);
const sat = (h, m = 0) => new Date(2026, 10, 7, h, m);
const sun = (h, m = 0) => new Date(2026, 10, 8, h, m);
const mon = (h, m = 0) => new Date(2026, 10, 9, h, m);

const cases = [
    ['24/7', wed(3), true],
    ['Mo-Fr 07:00-18:00; Sa,Su 08:00-17:00', wed(6, 59), false],
    ['Mo-Fr 07:00-18:00; Sa,Su 08:00-17:00', wed(7), true],
    ['Mo-Fr 07:00-18:00; Sa,Su 08:00-17:00', wed(18), false],
    ['Mo-Fr 07:00-18:00; Sa,Su 08:00-17:00', sun(9), true],
    ['Tu-Fr 07:00-13:00; Sa-Su 08:00-14:00; Mo off', mon(10), false],
    ['Mo-Su 08:00-12:00,13:00-22:00', wed(12, 30), false],
    ['Mo-Su 08:00-12:00,13:00-22:00', wed(13, 30), true],
    ['Fr-Sa 18:00-02:00', sun(1, 30), true],     // Saturday night, past midnight
    ['Fr-Sa 18:00-02:00', sun(2, 30), false],
    ['Fr-Sa 18:00-02:00', wed(20), false],
    ['08:00-20:00', sat(19), true],
    ['Mo-Fr 06:00-20:00, Sa-Su 08:00-18:00', sat(19), false],
    ['Mo-Fr 07:00-18:00; PH off', wed(10), true],
    ['Fr-Mo 09:00-17:00', sun(10), true],      // range wraps the week
    ['Fr-Mo 09:00-17:00', wed(10), false],
    ['sunrise-sunset', wed(12), null],
    ['Apr-Oct: 08:00-20:00', wed(12), null],
    ['', wed(12), null],
    [undefined, wed(12), null],
];
for (const [text, date, want] of cases) {
    assert.strictEqual(isOpenAt(text, date), want, `${JSON.stringify(text)} at ${date}`);
}
console.log(`hours.js: ${cases.length} cases passed`);
