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

    // The custom block's label and times only apply to shift code ETC.
    var shiftCode = document.getElementById('shift_code');
    var etcFields = document.getElementById('etc-fields');
    if (shiftCode && etcFields) {
        shiftCode.addEventListener('change', function () {
            etcFields.hidden = shiftCode.value !== 'ETC';
        });
    }
})();
