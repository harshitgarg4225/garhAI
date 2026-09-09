"""Downloads are named for the project, the artefact and the day.

Browser UAT run 10 saved the first PDF set as ``garh-export.pdf``: a name that said
nothing about which house or which day, so the second export of a second house would
have landed in Downloads as ``garh-export (1).pdf``. Sheets were ``A-01.pdf`` — every
project's A-01 the same file name. An architect's files are named for the project.

The name is decided at export time, where the project is in hand, and frozen on the
export record: the download route is unauthenticated (the token is the credential)
and has no project to ask. Sheet downloads load the project through the same
firm-scoped repository the sheet came from, so a foreign token still answers 404.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from garh_api import queue
from garh_api.repositories import SheetRepository
from garh_api.routers import sign_download_token
from garh_api.routers.sheets import (
    EXPORT_FILE_EXTENSIONS,
    EXPORT_FILE_LABELS,
    export_filename,
    file_slug,
    sheet_filename,
)

from tests import factories

WHEN = datetime(2026, 9, 9, 5, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# The helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Sharma Residence", "sharma-residence"),
        ("Sunrise Villa — Phase 2!", "sunrise-villa-phase-2"),
        ("Café Résidence", "cafe-residence"),
        ("  30x40 @ HSR  ", "30x40-hsr"),
        ("Mr. & Mrs. Rao's House", "mr-mrs-rao-s-house"),
    ],
)
def test_file_slug_folds_a_project_name_to_a_file_name_fragment(name: str, slug: str) -> None:
    assert file_slug(name) == slug


@pytest.mark.parametrize("name", ["", None, "!!!", "—", "   "])
def test_file_slug_never_answers_an_empty_stem(name: str | None) -> None:
    assert file_slug(name) == "project"
    assert file_slug(name, fallback="sheet") == "sheet"


def test_file_slug_is_capped_and_does_not_end_on_a_hyphen() -> None:
    long = " ".join(["residence"] * 20)
    slug = file_slug(long)
    assert len(slug) <= 48
    assert not slug.endswith("-")
    assert slug.startswith("residence-residence")


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("pdf-set", "sharma-residence-drawing-set-2026-09-09.pdf"),
        ("dxf", "sharma-residence-drawings-2026-09-09.dxf"),
        ("gltf", "sharma-residence-model-2026-09-09.glb"),
        ("png-pack", "sharma-residence-renders-2026-09-09.zip"),
    ],
)
def test_export_filename_names_project_artefact_and_day(kind: str, expected: str) -> None:
    assert export_filename("Sharma Residence", kind, WHEN) == expected


def test_every_export_kind_has_a_label_and_an_extension() -> None:
    """The two tables are read by the same helper; a kind in one and not the other
    would ship as ``<project>-<kind>-<date>.bin``."""
    assert set(EXPORT_FILE_LABELS) == set(EXPORT_FILE_EXTENSIONS) == set(queue.EXPORT_KINDS)


def test_export_filename_of_an_unknown_kind_is_still_a_file_name() -> None:
    assert export_filename("X", "weird kind!", WHEN) == "x-weird-kind-2026-09-09.bin"


def test_sheet_filename_keeps_the_sheet_number_as_printed() -> None:
    assert sheet_filename("Sharma Residence", "A-02A", "pdf") == "sharma-residence-A-02A.pdf"
    # The number is sanitised for a file system, never re-cased or reworded.
    assert sheet_filename("Sharma Residence", "A/02 A", "dxf") == "sharma-residence-A-02-A.dxf"
    assert sheet_filename("Sharma Residence", None, "svg") == "sharma-residence-sheet.svg"
    assert sheet_filename("", "A-01", "pdf") == "project-A-01.pdf"


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------


def _disposition(location: str) -> str | None:
    query = parse_qs(urlparse(location).query)
    values = query.get("response-content-disposition")
    return values[0] if values else None


@pytest.mark.integration
async def test_an_export_download_is_named_for_the_project(
    client: Any, api: str, session: Any, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    await factories.seed_plot_and_brief(session, firm_a, project_a.id)
    started = await client.post(
        "%s/projects/%s/export" % (api, project_a.id),
        json={"kind": "pdf-set"},
        headers=firm_a.headers,
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["id"]

    # The record froze the name at export time.
    record = await queue.get_export_job(str(firm_a.firm_id), job_id)
    assert record is not None
    file_name = record.params["fileName"]
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert file_name == "sharma-residence-drawing-set-%s.pdf" % today

    # Redeeming the link signs the object store's response as that attachment.
    token, _ = sign_download_token({"k": "export", "f": str(firm_a.firm_id), "j": job_id})
    redeemed = await client.get("%s/downloads/%s" % (api, token), follow_redirects=False)
    assert redeemed.status_code == 307, redeemed.text
    disposition = _disposition(redeemed.headers["location"])
    assert disposition == 'attachment; filename="%s"' % file_name
    # The negative control: the old generic stem is gone from the wire.
    assert "garh-export" not in redeemed.headers["location"]


@pytest.mark.integration
async def test_a_record_without_a_name_still_downloads_under_the_generic_stem(
    client: Any, api: str, clean_redis: Any, firm_a: Any, project_a: Any
) -> None:
    """A job written before names were kept has no ``fileName``; it must still
    redeem, and as a file with the right extension."""
    job_id = queue.new_job_id()
    await queue.put_export_job(
        queue.ExportJob(
            id=job_id,
            firm_id=str(firm_a.firm_id),
            project_id=str(project_a.id),
            kind="dxf",
            status="succeeded",
            params={"kind": "dxf"},
        )
    )
    token, _ = sign_download_token({"k": "export", "f": str(firm_a.firm_id), "j": job_id})
    redeemed = await client.get("%s/downloads/%s" % (api, token), follow_redirects=False)
    assert redeemed.status_code == 307, redeemed.text
    assert _disposition(redeemed.headers["location"]) == 'attachment; filename="garh-export.dxf"'


@pytest.mark.integration
async def test_a_sheet_download_is_named_for_the_project_and_the_sheet(
    client: Any, api: str, session: Any, firm_a: Any, project_a: Any
) -> None:
    repo = SheetRepository(session, firm_a.ctx())
    sheet = await repo.create(project_a.id, kind="floor", number="A-02A")
    await repo.set_layout(
        sheet.id,
        {"artifacts": {"pdf": "sheets/%s/%s/A-02A.pdf" % (firm_a.firm_id, project_a.id)}},
    )
    await session.commit()

    token, _ = sign_download_token(
        {"k": "sheet", "f": str(firm_a.firm_id), "s": str(sheet.id), "x": "pdf"}
    )
    redeemed = await client.get("%s/downloads/%s" % (api, token), follow_redirects=False)
    assert redeemed.status_code == 307, redeemed.text
    assert (
        _disposition(redeemed.headers["location"])
        == 'attachment; filename="sharma-residence-A-02A.pdf"'
    )


@pytest.mark.integration
async def test_a_sheet_token_for_another_firm_is_a_404_not_a_name(
    client: Any, api: str, session: Any, firm_a: Any, firm_b: Any, project_a: Any
) -> None:
    """The project lookup added for the name goes through the firm-scoped repository:
    a token that names firm B but firm A's sheet learns nothing, not even a name."""
    repo = SheetRepository(session, firm_a.ctx())
    sheet = await repo.create(project_a.id, kind="floor", number="A-01")
    await repo.set_layout(sheet.id, {"artifacts": {"pdf": "sheets/x/y/A-01.pdf"}})
    await session.commit()

    token, _ = sign_download_token(
        {"k": "sheet", "f": str(firm_b.firm_id), "s": str(sheet.id), "x": "pdf"}
    )
    redeemed = await client.get("%s/downloads/%s" % (api, token), follow_redirects=False)
    assert redeemed.status_code == 404, redeemed.text
