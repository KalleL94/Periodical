// Compare screen for proposing shift swaps (/swaps/new).
//
// Pick a colleague, tap one of your days, then one of theirs. /swaps/api/check
// decides whether the pair is allowed and gives rest warnings for the whole list.
// Allowed pairs collect in a list that a plain form posts to /swaps/propose as
// repeated req_date and tgt_date fields.
// With ?date= and no colleague, the "who can take this shift" panel from
// /swaps/api/day-takers comes first.
(function () {
    const root = document.getElementById('swap-compare');
    if (!root) return;

    const data = JSON.parse(document.getElementById('swap-data').textContent);
    const T = data.t;
    const MAX = parseInt(root.dataset.max, 10);
    const WEEKS = 5; // whole Mon to Sun weeks per window and per period step
    const HORIZON = parseInt(root.dataset.horizon, 10); // MAX_START_DAYS: /api/compare refuses a later start
    const preDate = root.dataset.date || null;
    const csrf = root.querySelector("input[name='csrf_token']").value;
    const $ = (id) => document.getElementById(id);
    const select = $('swap-target');
    const takersBox = $('swap-takers');
    const takersList = $('swap-takers-list');
    const grid = $('swap-grid');
    const statusEl = $('swap-status');
    const form = $('swap-propose-form');
    const pairsList = $('swap-pairs');
    const fields = $('swap-pair-fields');

    const state = { target: null, days: [], pairs: [], pendingOwn: null };
    let byDate = {};
    let suggested = {}; // return days for preDate, shown while preDate is the pending own day
    let loadSeq = 0; // bumped by every compare load, including period navigation
    let windowStart = null; // first day of the loaded window, as returned by the endpoint

    function el(tag, cls, text) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text != null) node.textContent = text;
        return node;
    }
    function parse(iso) {
        const p = iso.split('-');
        return new Date(+p[0], p[1] - 1, +p[2]);
    }
    function toIso(d) {
        const pad = (n) => String(n).padStart(2, '0');
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
    }
    function addDays(iso, n) {
        const d = parse(iso);
        d.setDate(d.getDate() + n);
        return toIso(d);
    }
    function monday(iso) {
        return addDays(iso, -((parse(iso).getDay() + 6) % 7));
    }
    function fmt(iso, opts) {
        return parse(iso).toLocaleDateString(data.lang, opts);
    }
    function dayLabel(iso) {
        return fmt(iso, { weekday: 'short', day: 'numeric', month: 'short' });
    }
    function say(text) {
        statusEl.textContent = text || '';
    }

    function api(url, opts) {
        opts = opts || {};
        opts.credentials = 'same-origin';
        opts.headers = Object.assign({ Accept: 'application/json' }, opts.headers);
        return fetch(url, opts).then((r) => {
            if (!r.ok) throw new Error(r.status);
            return r.json();
        });
    }
    function check(pairs) {
        return api('/swaps/api/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify({ target_id: state.target.id, pairs: pairs }),
        }).then((body) => body.results);
    }

    // ---- Day-first panel -------------------------------------------------

    function loadTakers() {
        takersBox.hidden = false;
        grid.hidden = true;
        form.hidden = true;
        $('swap-takers-date').textContent = dayLabel(preDate);
        api('/swaps/api/day-takers?date=' + preDate).then((body) => {
            takersList.textContent = '';
            body.colleagues.forEach((c) => {
                const ok = !c.blocked && c.kind;
                const li = el('li', 'swap-taker' + (ok ? '' : ' is-blocked'));
                const info = el('div', 'swap-taker-info');
                info.appendChild(el('strong', null, c.name));
                info.appendChild(el('span', 'text-muted', c.blocked || (c.kind ? T['takers_' + c.kind] : T.takers_none)));
                c.warnings.forEach((w) => info.appendChild(el('span', 'swap-warning', w)));
                li.appendChild(info);
                // Still selectable when blocked: a swap on another day can work.
                const btn = el('button', 'btn-compact' + (ok ? '' : ' secondary'), T.pick);
                btn.type = 'button';
                btn.dataset.id = c.id;
                li.appendChild(btn);
                takersList.appendChild(li);
            });
        }).catch(() => say(T.load_error));
    }

    takersList.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-id]');
        if (!btn) return;
        select.value = btn.dataset.id;
        loadTarget();
    });

    // ---- Compare grid ----------------------------------------------------

    function loadTarget() {
        const opt = select.options[select.selectedIndex];
        state.target = { id: parseInt(opt.value, 10), name: opt.textContent };
        state.pairs = [];
        state.pendingOwn = null;
        suggested = {};
        $('swap-target-id').value = state.target.id;
        takersBox.hidden = true;
        say('');
        // From the chosen day's Monday, WEEKS weeks covers the 4 weeks of suggestions after it.
        byDate = {};
        const seq = loadSeq + 1;
        loadWindow(preDate ? monday(preDate) : null, WEEKS).then((ok) => {
            if (!ok) return;
            if (preDate && byDate[preDate] && !byDate[preDate].past) {
                state.pendingOwn = preDate;
                findSuggestions(seq);
            }
            grid.hidden = false;
            form.hidden = false;
            render();
        });
    }

    // Load whole weeks from the Monday of start (null = this week) and make them the visible window. byDate
    // keeps days from earlier windows so chosen pairs outside the window still render.
    // Resolves true when applied, false when a newer load superseded it or it failed.
    function loadWindow(start, weeks) {
        const params = new URLSearchParams({ target_id: state.target.id, weeks: weeks });
        if (start) params.set('start', start);
        const seq = ++loadSeq;
        return api('/swaps/api/compare?' + params).then((body) => {
            if (seq !== loadSeq) return false;
            state.days = body.days;
            body.days.forEach((d) => { byDate[d.date] = d; });
            windowStart = body.days[0].date;
            return true;
        }).catch(() => {
            if (seq === loadSeq) say(T.load_error);
            return false;
        });
    }

    function goTo(start) {
        loadWindow(start, WEEKS).then((ok) => { if (ok) render(); });
    }

    function renderNav() {
        const bar = el('div', 'swap-nav');
        const today = toIso(new Date());
        const atStart = windowStart <= monday(today);
        // A step forward is offered only when the server would accept its start.
        const beyond = (days) => addDays(windowStart, days) > addDays(today, HORIZON);
        // Steps keep the start on a Monday; the server clamps an earlier start to this week.
        [['prev', T.prev, -7 * WEEKS, atStart],
         ['prev-week', T.prev_week, -7, atStart],
         ['today', T.today, null, atStart],
         ['next-week', T.next_week, 7, beyond(7)],
         ['next', T.next, 7 * WEEKS, beyond(7 * WEEKS)]].forEach(([name, label, step, off]) => {
            const btn = el('button', 'btn-compact secondary', label);
            btn.type = 'button';
            btn.dataset.nav = name;
            btn.disabled = off;
            btn.addEventListener('click', () => goTo(step === null ? null : addDays(windowStart, step)));
            bar.appendChild(btn);
        });
        const last = state.days[state.days.length - 1].date;
        const range = { day: 'numeric', month: 'short' };
        bar.appendChild(el('span', 'swap-range', fmt(windowStart, range) + ' \u2013 ' + fmt(last, range)));
        return bar;
    }

    // Days the colleague works and you are free, within 4 weeks after preDate,
    // nearest first, kept when check passes.
    function findSuggestions(seq) {
        const limit = addDays(preDate, 28);
        // At most 28 days qualify, well inside the /api/check cap; slice keeps that true if the cap drops.
        const cands = state.days
            .filter((d) => d.date > preDate && d.date <= limit && d.them.code !== 'OFF' && d.me.code === 'OFF')
            .slice(0, MAX);
        if (!cands.length) return;
        check(cands.map((d) => ({ req_date: preDate, tgt_date: d.date }))).then((results) => {
            if (seq !== loadSeq) return;
            results.forEach((r, i) => { if (!r.blocked) suggested[cands[i].date] = true; });
            render();
        }).catch(() => {});
    }

    function usedSlots() {
        const used = new Set();
        state.pairs.forEach((p) => slots(p.req, p.tgt).forEach((s) => used.add(s)));
        return used;
    }
    function slots(req, tgt) {
        return ['me|' + req, 'them|' + req, 'them|' + tgt, 'me|' + tgt];
    }

    function cell(side, iso, used) {
        const td = el('td');
        const day = byDate[iso];
        if (!day) return td;
        if (day.past) td.className = 'swap-day--past';
        const info = day[side];
        const btn = el('button', 'swap-cell', info.code === 'OFF' ? '-' : info.code);
        btn.type = 'button';
        btn.dataset.side = side;
        btn.dataset.date = iso;
        btn.disabled = !!day.past;
        btn.title = dayLabel(iso) + (info.start ? ' ' + info.start + '-' + info.end : '');
        const color = data.colors[info.code];
        if (color) {
            // Same effect as data-bg in shift-colors.js, which only runs on page load.
            btn.dataset.bg = color[0];
            btn.style.backgroundColor = color[0];
            btn.style.color = color[1];
        } else {
            btn.classList.add('is-off');
        }
        const selected = side === 'me' && iso === state.pendingOwn;
        btn.setAttribute('aria-pressed', selected ? 'true' : 'false');
        if (selected) btn.classList.add('is-selected');
        if (side === 'them' && state.pendingOwn === preDate && suggested[iso]) btn.classList.add('is-suggested');
        if (used.has(side + '|' + iso)) btn.classList.add('is-paired');
        td.appendChild(btn);
        return td;
    }

    function renderGrid() {
        grid.textContent = '';
        grid.appendChild(renderNav());
        const suggesting = state.pendingOwn && state.pendingOwn === preDate && Object.keys(suggested).length;
        grid.appendChild(el('p', 'swap-hint', state.pendingOwn ? T.tap_theirs + (suggesting ? ' ' + T.suggested : '') : T.tap_mine));
        const used = usedSlots();
        const weeks = [];
        state.days.forEach((d) => {
            const m = monday(d.date);
            if (weeks[weeks.length - 1] !== m) weeks.push(m);
        });
        weeks.forEach((m) => {
            const table = el('table', 'swap-week keep-table');
            const head = el('tr');
            head.appendChild(el('th'));
            const rowMe = el('tr');
            const rowThem = el('tr');
            rowMe.appendChild(el('th', 'swap-who', T.you));
            rowThem.appendChild(el('th', 'swap-who', state.target.name));
            for (let i = 0; i < 7; i++) {
                const iso = addDays(m, i);
                const th = el('th', 'swap-day' + (byDate[iso] && byDate[iso].past ? ' swap-day--past' : ''));
                th.appendChild(el('span', null, fmt(iso, { weekday: 'short' })));
                th.appendChild(el('span', 'swap-day-num', fmt(iso, { day: 'numeric', month: 'numeric' })));
                head.appendChild(th);
                rowMe.appendChild(cell('me', iso, used));
                rowThem.appendChild(cell('them', iso, used));
            }
            const thead = el('thead');
            thead.appendChild(head);
            const tbody = el('tbody');
            tbody.appendChild(rowMe);
            tbody.appendChild(rowThem);
            table.appendChild(thead);
            table.appendChild(tbody);
            grid.appendChild(table);
        });
    }

    function renderPairs() {
        pairsList.textContent = '';
        fields.textContent = '';
        state.pairs.forEach((p, i) => {
            const li = el('li', 'swap-pair');
            const body = el('div', 'swap-pair-body');
            body.appendChild(el('strong', null, T['kind_' + p.kind]));
            const give = byDate[p.req] ? byDate[p.req].me.code : '';
            const get = byDate[p.tgt] ? byDate[p.tgt].them.code : '';
            if (give && give !== 'OFF') body.appendChild(el('span', null, T.you_give + ': ' + give + ', ' + dayLabel(p.req)));
            if (get && get !== 'OFF') body.appendChild(el('span', null, T.you_get + ': ' + get + ', ' + dayLabel(p.tgt)));
            p.warnings.forEach((w) => body.appendChild(el('span', 'swap-warning', w)));
            li.appendChild(body);
            const rm = el('button', 'btn-xs secondary', T.remove);
            rm.type = 'button';
            rm.dataset.index = i;
            li.appendChild(rm);
            pairsList.appendChild(li);
            [['req_date', p.req], ['tgt_date', p.tgt]].forEach(([name, value]) => {
                const input = el('input');
                input.type = 'hidden';
                input.name = name;
                input.value = value;
                fields.appendChild(input);
            });
        });
        $('swap-no-pairs').hidden = state.pairs.length > 0;
        $('swap-send').disabled = state.pairs.length === 0;
    }

    function render() {
        renderGrid();
        renderPairs();
    }

    function pairBody(p) {
        return { req_date: p.req, tgt_date: p.tgt };
    }
    function sameList(list) {
        return list.length === state.pairs.length && list.every((p, i) => p === state.pairs[i]);
    }
    function setWarnings(list, results) {
        list.forEach((p, i) => { p.warnings = results[i].warnings; });
    }

    // Rest warnings depend on the whole list, so every change re-checks all pairs together.
    function refreshWarnings() {
        const list = state.pairs.slice();
        if (!list.length) return;
        check(list.map(pairBody)).then((results) => {
            if (!sameList(list)) return; // a newer change runs its own check
            setWarnings(list, results);
            render();
        }).catch(() => say(T.load_error));
    }

    function addPair(req, tgt) {
        if (state.pairs.length >= MAX) return say(T.max.replace('{n}', MAX));
        const clash = () => slots(req, tgt).some((s) => usedSlots().has(s));
        if (clash()) return say(T.slot_used);
        const target = state.target; // navigating periods must not drop this answer
        const list = state.pairs.slice();
        // The candidate goes last, after the pairs already chosen.
        check(list.map(pairBody).concat([{ req_date: req, tgt_date: tgt }])).then((results) => {
            if (target !== state.target) return;
            const r = results[results.length - 1];
            if (r.blocked) return say(r.blocked);
            if (clash() || state.pairs.length >= MAX) return; // a double tap already added it
            const fresh = sameList(list);
            if (fresh) setWarnings(list, results);
            state.pairs.push({ req: req, tgt: tgt, kind: r.kind, warnings: r.warnings });
            state.pendingOwn = null;
            say('');
            render();
            if (!fresh) refreshWarnings(); // the list changed while the check ran
        }).catch(() => say(T.load_error));
    }

    grid.addEventListener('click', (e) => {
        const btn = e.target.closest('.swap-cell');
        if (!btn || btn.disabled) return;
        const iso = btn.dataset.date;
        if (btn.dataset.side === 'me') {
            state.pendingOwn = state.pendingOwn === iso ? null : iso;
            say('');
            render();
        } else if (!state.pendingOwn) {
            say(T.tap_mine);
        } else {
            addPair(state.pendingOwn, iso);
        }
    });

    pairsList.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-index]');
        if (!btn) return;
        state.pairs.splice(parseInt(btn.dataset.index, 10), 1);
        say('');
        render();
        refreshWarnings();
    });

    form.addEventListener('submit', () => {
        $('swap-send').disabled = true;
    });

    select.addEventListener('change', () => {
        if (select.value) {
            loadTarget();
            return;
        }
        loadSeq++;
        state.target = null;
        say('');
        grid.hidden = true;
        form.hidden = true;
        if (preDate) loadTakers();
    });

    // ---- Start -----------------------------------------------------------

    const withId = root.dataset.with;
    if (withId && select.querySelector('option[value="' + withId + '"]')) {
        select.value = withId;
        loadTarget();
    } else if (preDate) {
        loadTakers();
    }
})();
