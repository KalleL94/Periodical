// Apply data-driven colours without inline style attributes.
//
// Shift colours are admin-configured in the database, so they cannot live in
// static CSS. Templates emit the colour in data attributes and this script
// copies it onto the element's style at runtime (which is CSP-safe, unlike an
// inline style attribute):
//
//   data-bg          -> background-color   (shift pills / badges)
//   data-fg          -> color              (text tinted with the shift colour)
//   data-border-top  -> border-top-color   (calendar day cells)
//   data-border-left -> border-left-color  (per-person schedule rows)
(function () {
    function apply() {
        document.querySelectorAll('[data-bg]').forEach(function (el) {
            el.style.backgroundColor = el.getAttribute('data-bg');
            var fg = el.getAttribute('data-fg');
            if (fg) el.style.color = fg;
        });
        document.querySelectorAll('[data-fg]:not([data-bg])').forEach(function (el) {
            el.style.color = el.getAttribute('data-fg');
        });
        document.querySelectorAll('[data-border-top]').forEach(function (el) {
            el.style.borderTopColor = el.getAttribute('data-border-top');
        });
        document.querySelectorAll('[data-border-left]').forEach(function (el) {
            el.style.borderLeftColor = el.getAttribute('data-border-left');
        });
    }
    if (document.readyState !== 'loading') {
        apply();
    } else {
        document.addEventListener('DOMContentLoaded', apply);
    }
})();
