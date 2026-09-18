// Behaviour for _day_edit_panel.html.
//
// This lives with the component, not with the day page. The partial is rendered
// on the day view and in the month and week multi-day drawer; when this code sat
// in day.html the drawer got the markup without it, so every tab showed at once
// and the hours field never filled itself.
//
// Every block returns early when its elements are absent, so loading this from
// the base layout is free on pages with no edit panel.
(function () {
    // Tabs: one panel visible at a time.
    var tabs = document.querySelectorAll('.day-tab');
    tabs.forEach(function (btn) {
        btn.addEventListener('click', function () {
            var name = btn.dataset.tab;
            tabs.forEach(function (b) {
                b.classList.toggle('is-active', b === btn);
            });
            document.querySelectorAll('.day-tab-panel').forEach(function (panel) {
                panel.classList.toggle('is-active', panel.id === 'tab-' + name);
            });
        });
    });

    // Fill the hours field from the two time fields, crossing midnight on a
    // negative difference (an evening shift extended until 00:30).
    var startInput = document.getElementById('seg_start');
    var endInput = document.getElementById('seg_end');
    var hoursInput = document.getElementById('seg_hours');
    if (startInput && endInput && hoursInput) {
        var minutes = function (value) {
            var parts = value.split(':');
            return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
        };

        var updateHours = function () {
            if (!startInput.value || !endInput.value) return;
            var diff = minutes(endInput.value) - minutes(startInput.value);
            if (diff <= 0) diff += 24 * 60;
            hoursInput.value = (diff / 60).toFixed(2);
        };

        startInput.addEventListener('change', updateHours);
        endInput.addEventListener('change', updateHours);

        document.querySelectorAll('.ot-preset').forEach(function (btn) {
            btn.addEventListener('click', function () {
                startInput.value = btn.dataset.start;
                endInput.value = btn.dataset.end;
                updateHours();
            });
        });
    }

    // Per-day rows are built here, not on the server: the drawer renders before
    // anything is selected, so the server never knows which days the table needs.
    var rowTemplate = document.getElementById('per-day-row');
    var rowBody = document.getElementById('per-day-rows');
    var areaSelect = document.getElementById('per_day_area');
    var perDayTable = document.getElementById('per-day-table');

    if (rowTemplate && rowBody && areaSelect && perDayTable) {
        var dayLabel = function (iso) {
            var d = new Date(iso + 'T00:00:00');
            return d.toLocaleDateString(document.documentElement.lang || 'sv', {
                weekday: 'short', day: 'numeric', month: 'short'
            });
        };

        document.addEventListener('day-selection-change', function (event) {
            rowBody.textContent = '';
            event.detail.dates.forEach(function (iso) {
                var row = rowTemplate.content.cloneNode(true);
                row.querySelector('.per-day-date').textContent = dayLabel(iso);
                row.querySelectorAll('[data-field]').forEach(function (el) {
                    el.name = el.dataset.field + '_' + iso;
                });
                rowBody.appendChild(row);
            });
        });

        areaSelect.addEventListener('change', function () {
            ['absence', 'time', 'shift'].forEach(function (area) {
                perDayTable.classList.toggle('is-area-' + area, areaSelect.value === area);
            });
        });
    }

    // The custom block's label and times only apply to shift code ETC.
    var shiftCode = document.getElementById('shift_code');
    var etcFields = document.getElementById('etc-fields');
    if (shiftCode && etcFields) {
        shiftCode.addEventListener('change', function () {
            etcFields.hidden = shiftCode.value !== 'ETC';
        });
    }
})();
