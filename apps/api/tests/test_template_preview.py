"""The picker thumbnail is the sheet renderer's own output, not a third drawing."""

from __future__ import annotations

import pytest
from garh_api.template_preview import preview_data_url, preview_svg
from garh_model.testing import opening_ops, ops_to_json, two_room_plan_ops

pytest.importorskip("services.drawings.render.svg", reason="drawings renderer not importable")


def _recipe() -> list[dict]:
    return ops_to_json([*two_room_plan_ops(), *opening_ops()])


def test_a_plan_renders_to_a_standalone_svg_of_its_fabric() -> None:
    svg = preview_svg(_recipe())
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ')
    assert 'data-layer="A-WALL"' in svg
    assert (
        "<text" not in svg
    ), "2 px room names are grey fuzz at picker size; the card names the plan"
    assert "A-DIM" not in svg, "dimensions are noise at thumbnail size"
    assert "url(#" not in svg, "a fragment cannot reference the sheet's clip-path definitions"
    assert "<script" not in svg


def test_the_preview_is_deterministic() -> None:
    assert preview_svg(_recipe()) == preview_svg(_recipe())


def test_a_recipe_without_walls_is_refused_not_blank() -> None:
    from garh_model.testing import two_room_plan_ops as ops

    storeys_only = [o for o in ops_to_json(ops()) if o["type"].startswith(("plot.", "storey."))]
    with pytest.raises(ValueError):
        preview_svg(storeys_only)


def test_the_data_url_is_an_image_not_a_document() -> None:
    url = preview_data_url(preview_svg(_recipe()))
    assert url.startswith("data:image/svg+xml;charset=utf-8,%3Csvg")
    assert "<" not in url[len("data:image/svg+xml;charset=utf-8,") :]
