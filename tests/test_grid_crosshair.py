"""The team grids' hover crosshair depends on markup the templates emit.

The column half of the crosshair is JavaScript that matches a hovered cell's
data-person against every other cell's. Nothing fails loudly if a template
stops emitting the attribute: the page renders, the highlight just quietly
stops following the pointer. These tests pin the contract instead.
"""

import re

import pytest


@pytest.mark.parametrize("path", ["/month", "/year"])
def test_person_columns_are_addressable(test_client, path):
    """Header and body cells carry the same data-person key."""
    response = test_client.get(path)
    assert response.status_code == 200

    headers = set(re.findall(r'<th class="person-header[^"]*" data-person="([^"]+)"', response.text))
    cells = set(re.findall(r'<td class="day-cell month-shift-cell[^"]*" data-person="([^"]+)"', response.text))

    assert headers, f"{path} renders no person headers with data-person"
    assert cells, f"{path} renders no shift cells with data-person"
    assert cells <= headers, f"{path} has shift cells whose data-person has no header: {cells - headers}"


def test_crosshair_script_is_loaded(test_client):
    """base.html ships the script that lights the hovered column."""
    response = test_client.get("/month")
    assert "/static/js/grid-crosshair.js" in response.text
