// Select days in the month and week calendars.
//
// Both views render identical markup (table.calendar-grid > td.calendar-day),
// so one script drives both. The day-number link keeps working: a click on it
// navigates and never toggles.
//
// Emits `day-selection-change` on document with detail.dates, the sorted list of
// YYYY-MM-DD strings, so the edit drawer stays independent of this file.
(function () {
    var cells = Array.prototype.slice.call(document.querySelectorAll('td.calendar-day[data-date]'));
    if (!cells.length) return;

    var lastIndex = null;

    function selected() {
        return cells
            .filter(function (c) { return c.classList.contains('is-selected'); })
            .map(function (c) { return c.dataset.date; })
            .sort();
    }

    function announce() {
        document.dispatchEvent(new CustomEvent('day-selection-change', {
            detail: { dates: selected() }
        }));
    }

    function setSelected(cell, on) {
        cell.classList.toggle('is-selected', on);
        cell.setAttribute('aria-pressed', on ? 'true' : 'false');
    }

    function clearAll() {
        cells.forEach(function (c) { setSelected(c, false); });
        lastIndex = null;
        announce();
    }

    cells.forEach(function (cell, index) {
        cell.addEventListener('click', function (event) {
            // The date link navigates; everything else in the cell selects.
            if (event.target.closest('a')) return;

            if (event.shiftKey && lastIndex !== null) {
                var from = Math.min(lastIndex, index);
                var to = Math.max(lastIndex, index);
                for (var i = from; i <= to; i++) setSelected(cells[i], true);
            } else {
                setSelected(cell, !cell.classList.contains('is-selected'));
                lastIndex = index;
            }
            announce();
        });
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') clearAll();
    });

    document.addEventListener('day-selection-clear', clearAll);
})();
