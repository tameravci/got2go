// Reads OpenStreetMap opening_hours text and tells if a place is open now.
//
// It covers the common forms:
//   "24/7"
//   "Mo-Fr 07:00-18:00; Sa,Su 08:00-17:00"
//   "Tu-Fr 07:00-13:00; Sa-Su 08:00-14:00; Mo off"
//   "Mo-Su 08:00-12:00,13:00-22:00"
//   "Fr-Sa 18:00-02:00"           (past midnight)
//   "08:00-20:00"                 (every day)
// Rules for public holidays (PH, SH) are skipped. Anything else, such as
// month ranges or "sunrise-sunset", returns null ("unknown"), and the map
// shows the normal pin.
(function (root) {
    const DAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];  // Date.getDay() order
    const DAY = '(?:Mo|Tu|We|Th|Fr|Sa|Su)';
    const DAYS_RE = new RegExp(`^(${DAY}(?:\\s*-\\s*${DAY})?(?:\\s*,\\s*${DAY}(?:\\s*-\\s*${DAY})?)*)\\s*(.*)$`);
    const TIME_RE = /^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$/;
    const OPEN_END_RE = /^(\d{1,2}):(\d{2})\+$/;

    function parseDays(spec) {
        const days = new Set();
        for (const part of spec.split(',')) {
            const [a, b] = part.split('-').map(s => DAYS.indexOf(s.trim()));
            if (b === undefined) { days.add(a); continue; }
            // A range can wrap around the week, for example Fr-Mo.
            for (let d = a; ; d = (d + 1) % 7) {
                days.add(d);
                if (d === b) break;
            }
        }
        return days;
    }

    // Returns a list of [startMinute, endMinute] or null if unreadable.
    // An end past midnight is stored as more than 1440.
    function parseTimes(spec) {
        const s = spec.trim().toLowerCase();
        if (s === 'off' || s === 'closed') return [];
        if (s === '' || s === '24/7') return [[0, 1440]];
        const ranges = [];
        for (const part of spec.split(',')) {
            const p = part.trim();
            let m = TIME_RE.exec(p);
            if (m) {
                const start = +m[1] * 60 + +m[2];
                let end = +m[3] * 60 + +m[4];
                if (end <= start) end += 1440;
                ranges.push([start, end]);
                continue;
            }
            m = OPEN_END_RE.exec(p);
            if (m) { ranges.push([+m[1] * 60 + +m[2], 1440]); continue; }
            return null;
        }
        return ranges;
    }

    // Returns {0: [[start, end]], 1: [...], ...} by weekday, or null.
    function parseSchedule(text) {
        if (!text || typeof text !== 'string') return null;
        const trimmed = text.trim();
        if (trimmed === '24/7') {
            const all = {};
            for (let d = 0; d < 7; d++) all[d] = [[0, 1440]];
            return all;
        }
        const schedule = {};
        // Rules split on ";" and on ", " when a new day list follows.
        const rules = trimmed.split(new RegExp(`;|,\\s*(?=(?:${DAY}|PH|SH)\\b)`));
        let any = false;
        for (const raw of rules) {
            const rule = raw.trim();
            if (!rule) continue;
            if (/^(PH|SH)\b/.test(rule)) continue;
            let days, rest;
            const m = DAYS_RE.exec(rule);
            if (m) {
                days = parseDays(m[1].replace(/\s+/g, ''));
                rest = m[2];
            } else if (/^\d/.test(rule) || /^(off|closed)$/i.test(rule)) {
                days = new Set([0, 1, 2, 3, 4, 5, 6]);
                rest = rule;
            } else {
                return null;
            }
            const times = parseTimes(rest);
            if (times === null) return null;
            // A later rule replaces an earlier one for the days it names.
            for (const d of days) schedule[d] = times;
            any = true;
        }
        return any ? schedule : null;
    }

    // true = open, false = closed, null = unknown.
    function isOpenAt(text, date) {
        const schedule = parseSchedule(text);
        if (!schedule) return null;
        const day = date.getDay();
        const minute = date.getHours() * 60 + date.getMinutes();
        for (const [start, end] of schedule[day] || []) {
            if (minute >= start && minute < end) return true;
        }
        // Yesterday's hours can run past midnight into today.
        for (const [, end] of schedule[(day + 6) % 7] || []) {
            if (end > 1440 && minute + 1440 < end) return true;
        }
        return false;
    }

    const api = { parseSchedule, isOpenAt, isOpenNow: text => isOpenAt(text, new Date()) };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.OpeningHours = api;
})(this);
