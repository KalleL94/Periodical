// Column half of the crosshair on the team month and year grids.
//
// The week grid does the whole thing in CSS (one rule per column index), but
// there the columns are the seven days. On the month and year grids the
// columns are people, their count varies, and they are addressed by
// data-person, which no CSS selector can match against the hovered cell. The
// row half stays in CSS (tr.shift-row:hover).
(function () {
    function init() {
        var grid = document.querySelector('.month-schedule');
        if (!grid || !window.matchMedia('(hover: hover)').matches) return;

        var cells = grid.querySelectorAll('[data-person]');
        var current = null;

        function light(person) {
            if (person === current) return;
            cells.forEach(function (cell) {
                cell.classList.toggle('col-hl', person !== null && cell.dataset.person === person);
            });
            current = person;
        }

        grid.addEventListener('mouseover', function (e) {
            var cell = e.target.closest('[data-person]');
            light(cell ? cell.dataset.person : null);
        });
        grid.addEventListener('mouseleave', function () { light(null); });
    }
    if (document.readyState !== 'loading') {
        init();
    } else {
        document.addEventListener('DOMContentLoaded', init);
    }
})();
