"""The ``drawings.generate_sheets`` / ``drawings.export`` envelope contract.

The handler is exercised for real — ``await handler.handle(ctx)`` — against a fake
:class:`JobContext` whose blob client writes to a temp directory and whose progress
reporter records events in a list. That is enough to prove the parts a pure pipeline
test cannot:

* the API's envelope keys (``assets["model"]``, ``assets["areas"]``,
  ``outputs["<slug>.<fmt>"]``, ``outputs["export"]``) are the ones the handler reads;
* every published sheet ends up with a download URL in ``layout.artifacts``, which is
  exactly where ``garh_api.routers.jobs._sheet_artifacts`` looks for it;
* progress is monotonic and ends at 100 — §15's "never a fake bar" cuts both ways, a
  bar that never reaches the end is just as dishonest;
* a missing format degrades to a note, and a missing model fails with an action.

    python3 services/drawings/tests/test_handler_sheets.py
    pytest -q services/drawings/tests/test_handler_sheets.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _path in (_ROOT, os.path.join(_ROOT, "apps", "api")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.dev_stubs import install_worker_dep_stubs  # noqa: E402

install_worker_dep_stubs()

from services.common.envelope import BlobRef, JobEnvelope  # noqa: E402
from services.common.errors import InvalidJobError  # noqa: E402
from services.drawings import handler as handler_module  # noqa: E402
from services.drawings.handler import (  # noqa: E402
    ASSET_AREAS,
    ASSET_MODEL,
    OUTPUT_EXPORT,
    DrawingsJobHandler,
)

# Reuse the pipeline test's fixtures: one fold, one evaluation, both real.
sys.path.insert(0, _HERE)
from test_pipeline import TITLE_BLOCK, load_areas, load_document  # noqa: E402


# ---------------------------------------------------------------------------
# A JobContext that is real enough
# ---------------------------------------------------------------------------
@dataclass
class RecordedEvent:
    type: str
    stage: str
    message: str
    percent: Optional[int]
    data: Dict[str, Any] = field(default_factory=dict)


class FakeProgress:
    def __init__(self) -> None:
        self.events: List[RecordedEvent] = []

    async def stage(
        self, stage: str, message: str, *, percent: Optional[int] = None, **data: Any
    ) -> None:
        self.events.append(RecordedEvent("stage", stage, message, percent, dict(data)))

    async def artifact(self, name: str, **data: Any) -> None:
        self.events.append(RecordedEvent("artifact", name, name, None, dict(data)))

    async def warning(self, message: str, **data: Any) -> None:
        self.events.append(RecordedEvent("warning", "", message, None, dict(data)))

    def percents(self) -> List[int]:
        return [e.percent for e in self.events if e.percent is not None]


class FakeBlobs:
    """Writes to ``path`` refs. Same interface the real client exposes to a handler."""

    def __init__(self) -> None:
        self.written: Dict[str, bytes] = {}

    async def fetch(self, ref: Any, *, what: str = "file") -> bytes:
        if ref.inline_base64 is not None:
            import base64

            return base64.b64decode(ref.inline_base64)
        if ref.path:
            with open(ref.path, "rb") as handle:
                return handle.read()
        raise AssertionError("fake blobs only handle inline/path refs, got %r" % (ref,))

    async def put(
        self, ref: Any, data: bytes, *, content_type: Optional[str] = None, what: str = "result"
    ) -> Any:
        assert ref.path, "every output the handler writes must carry a destination"
        os.makedirs(os.path.dirname(ref.path), exist_ok=True)
        with open(ref.path, "wb") as handle:
            handle.write(data)
        self.written[ref.path] = data
        return BlobRef(
            path=ref.path, get_url=ref.get_url, key=ref.key, content_type=content_type
        )


@dataclass
class FakeSettings:
    max_dxf_upload_bytes: int = 20 * 1024 * 1024
    dxf_parse_timeout_seconds: int = 10
    dxf_parse_memory_limit_mb: int = 512


@dataclass
class FakeContext:
    envelope: JobEnvelope
    settings: FakeSettings
    progress: FakeProgress
    blobs: FakeBlobs
    cancel_event: Any = None
    checkpoint: Any = None

    @property
    def payload(self) -> Dict[str, Any]:
        return self.envelope.payload

    def raise_if_cancelled(self) -> None:
        return None


class Workspace:
    """A temp directory holding the model asset and the sheet outputs."""

    def __init__(self, *, with_areas: bool = True, formats=("svg",)):
        self.root = tempfile.mkdtemp(prefix="garh-handler-")
        self.document = load_document()
        self.model_path = os.path.join(self.root, "model.json")
        with open(self.model_path, "w", encoding="utf-8") as handle:
            json.dump(self.document, handle)
        self.areas_path = None
        if with_areas:
            self.areas_path = os.path.join(self.root, "areas.json")
            with open(self.areas_path, "w", encoding="utf-8") as handle:
                json.dump(load_areas(self.document), handle)
        self.formats = tuple(formats)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    # -- envelopes ------------------------------------------------------
    def assets(self) -> Dict[str, BlobRef]:
        assets = {ASSET_MODEL: BlobRef(path=self.model_path, content_type="application/json")}
        if self.areas_path:
            assets[ASSET_AREAS] = BlobRef(
                path=self.areas_path, content_type="application/json"
            )
        return assets

    def sheet_outputs(self, slugs) -> Dict[str, BlobRef]:
        """Presigned-PUT stand-ins, keyed exactly as the API mints them."""
        outputs: Dict[str, BlobRef] = {}
        for slug in slugs:
            for fmt in self.formats:
                key = "%s.%s" % (slug, fmt)
                path = os.path.join(self.root, "out", key)
                outputs[key] = BlobRef(
                    path=path, get_url="https://example.invalid/%s" % key, key=key
                )
        return outputs

    def sheets_context(self, *, payload_extra=None, slugs=None) -> FakeContext:
        payload = {
            "designVersionId": "11111111-1111-1111-1111-111111111111",
            "kinds": None,
            "scaleDenominator": 100,
            "sheetSize": "A2",
            "dimToJamb": False,
            "titleBlock": TITLE_BLOCK,
            "revisions": [{"revision": "A", "date": "01-01-2026", "note": "First submission"}],
            "formats": list(self.formats),
        }
        payload.update(payload_extra or {})
        storeys = [s["id"] for s in self.document["house"]["storeys"]]
        slugs = slugs or (
            ["site-plan", "section-a", "door-window-schedule", "area-statement"]
            + ["elevation-%s" % d for d in "nesw"]
            + ["floor-plan-%s" % sid for sid in storeys]
        )
        envelope = JobEnvelope(
            job_id="job-sheets-test",
            kind="drawings.generate_sheets",
            firm_id="firm-test",
            project_id="project-test",
            design_version_id=payload["designVersionId"],
            payload=payload,
            assets=self.assets(),
            outputs=self.sheet_outputs(slugs),
        )
        return FakeContext(envelope, FakeSettings(), FakeProgress(), FakeBlobs())

    def export_context(self, kind: str, **payload_extra) -> FakeContext:
        payload = {
            "kind": kind,
            "designVersionId": "11111111-1111-1111-1111-111111111111",
            "sheetIds": [],
            "includeDisclaimer": True,
            "options": {},
        }
        payload.update(payload_extra)
        envelope = JobEnvelope(
            job_id="job-export-test",
            kind="drawings.export",
            firm_id="firm-test",
            project_id="project-test",
            design_version_id=payload["designVersionId"],
            payload=payload,
            assets=self.assets(),
            outputs={
                OUTPUT_EXPORT: BlobRef(
                    path=os.path.join(self.root, "export.bin"),
                    get_url="https://example.invalid/export",
                    key="export",
                )
            },
        )
        return FakeContext(envelope, FakeSettings(), FakeProgress(), FakeBlobs())


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


HANDLER = DrawingsJobHandler()


# ---------------------------------------------------------------------------
# Sheet generation
# ---------------------------------------------------------------------------
def test_the_handler_draws_and_publishes_the_whole_set():
    workspace = Workspace()
    try:
        ctx = workspace.sheets_context()
        result = run(HANDLER.handle(ctx))
        layouts = result.data["sheets"]
        assert len(layouts) >= 6, layouts
        for layout in layouts:
            # Every sheet reports its SVG at exactly the key
            # `_sheet_artifacts` in routers/jobs.py reads — and reports the object
            # KEY, not a presigned URL, because the API re-signs per request.
            assert layout["artifacts"]["svg"] == "%s.svg" % layout["sheetId"]
            path = os.path.join(workspace.root, "out", "%s.svg" % layout["sheetId"])
            assert os.path.exists(path), layout["sheetId"]
            with open(path, "r", encoding="utf-8") as handle:
                assert handle.read().startswith("<svg ")
        assert result.data["designVersionId"] == "11111111-1111-1111-1111-111111111111"
        assert result.data["chainCount"] > 0
        assert result.message.startswith("Drew ")
    finally:
        workspace.close()


def test_progress_is_monotonic_and_finishes_at_a_hundred():
    workspace = Workspace()
    try:
        ctx = workspace.sheets_context()
        run(HANDLER.handle(ctx))
        percents = ctx.progress.percents()
        assert percents == sorted(percents), percents
        assert percents[-1] == 100
        # One event per sheet, naming the sheet — §15's staged, honest messages.
        drew = [e for e in ctx.progress.events if e.message.startswith("Drew ")]
        assert len(drew) >= 6
        assert all(e.data.get("sheetId") for e in drew)
    finally:
        workspace.close()


def test_timings_say_where_the_five_minute_budget_went():
    """§14 asks for the budget; "note where the time goes" needs it measured."""
    workspace = Workspace()
    try:
        result = run(HANDLER.handle(workspace.sheets_context()))
        timings = result.data["timings"]
        assert set(timings) == {"loadMs", "drawMs", "publishMs", "totalMs"}
        assert timings["totalMs"] >= timings["drawMs"] >= 0
        assert timings["totalMs"] < 300_000, timings
        globals()["_LAST_TIMINGS"] = timings
        globals()["_LAST_SHEETS"] = len(result.data["sheets"])
    finally:
        workspace.close()


def test_dxf_absence_becomes_a_note_not_a_failed_job():
    """A worker image without ezdxf must still deliver the drawings.

    This is the behaviour change that matters most in this file: the old worker
    refused to boot at all without ezdxf, so a missing download format cost the
    architect every sheet.
    """
    workspace = Workspace(formats=("svg", "dxf"))
    try:
        result = run(HANDLER.handle(workspace.sheets_context()))
        try:
            import ezdxf  # noqa: F401

            has_ezdxf = True
        except ImportError:
            has_ezdxf = False
        if has_ezdxf:
            assert all(layout["artifacts"].get("dxf") for layout in result.data["sheets"])
            return
        assert any("DXF" in note for note in result.data["notes"]), result.data["notes"]
        for layout in result.data["sheets"]:
            assert "dxf" not in layout["artifacts"]
            assert layout["artifacts"]["svg"]  # the set still ships
    finally:
        workspace.close()


def test_a_sheet_with_no_minted_output_is_still_drawn_and_reported():
    """Artifacts are optional; the sheet row is not. A set is not a download list."""
    workspace = Workspace()
    try:
        ctx = workspace.sheets_context(slugs=["site-plan"])
        result = run(HANDLER.handle(ctx))
        by_slug = {layout["sheetId"]: layout for layout in result.data["sheets"]}
        assert by_slug["site-plan"]["artifacts"]["svg"] == "site-plan.svg"
        assert by_slug["door-window-schedule"]["artifacts"] == {}
        assert by_slug["door-window-schedule"]["stats"]["primitives"] > 0
    finally:
        workspace.close()


def test_svg_is_forced_into_the_format_list():
    """The viewer reads SVG. Asking for DXF only would leave the tab blank."""
    workspace = Workspace(formats=("dxf",))
    try:
        ctx = workspace.sheets_context(payload_extra={"formats": ["dxf"]})
        result = run(HANDLER.handle(ctx))
        assert result.data["formats"][0] == "svg"
    finally:
        workspace.close()


def test_missing_area_statement_skips_one_sheet_with_a_note():
    workspace = Workspace(with_areas=False)
    try:
        result = run(HANDLER.handle(workspace.sheets_context()))
        kinds = {layout["kind"] for layout in result.data["sheets"]}
        assert "area-statement" not in kinds
        assert any("compliance" in note for note in result.data["notes"])
    finally:
        workspace.close()


def test_both_kind_spellings_are_accepted_by_the_worker():
    """The API validates the DB vocabulary; the worker must not reject it.

    ``kinds:["floor"]`` used to pass ``SheetsGenerateIn`` and then fail here.
    """
    workspace = Workspace()
    try:
        for spelling in ("floor", "floor-plan"):
            ctx = workspace.sheets_context(payload_extra={"kinds": [spelling]})
            result = run(HANDLER.handle(ctx))
            assert {layout["kind"] for layout in result.data["sheets"]} == {"floor"}, spelling
    finally:
        workspace.close()


def test_an_unknown_kind_fails_with_an_action():
    workspace = Workspace()
    try:
        ctx = workspace.sheets_context(payload_extra={"kinds": ["roof"]})
        try:
            run(HANDLER.handle(ctx))
        except InvalidJobError as exc:
            assert exc.action
            assert "roof" in str(exc.detail)
        else:  # pragma: no cover
            raise AssertionError("an unknown kind must fail the job")
    finally:
        workspace.close()


def test_a_sheets_job_without_a_design_version_is_refused():
    workspace = Workspace()
    try:
        ctx = workspace.sheets_context()
        ctx = FakeContext(
            dataclasses.replace(ctx.envelope, design_version_id=None),
            ctx.settings,
            ctx.progress,
            ctx.blobs,
        )
        try:
            run(HANDLER.handle(ctx))
        except InvalidJobError as exc:
            assert "Save the design" in (exc.action or "")
        else:  # pragma: no cover
            raise AssertionError("sheets must pin to a version")
    finally:
        workspace.close()


def test_a_missing_model_asset_names_the_asset():
    workspace = Workspace()
    try:
        ctx = workspace.sheets_context()
        ctx.envelope.assets.pop(ASSET_MODEL)
        try:
            run(HANDLER.handle(ctx))
        except Exception as exc:  # noqa: BLE001 - require_asset raises its own type
            assert "model" in str(exc).lower() or "design" in str(exc).lower()
        else:  # pragma: no cover
            raise AssertionError("no model asset means no sheets")
    finally:
        workspace.close()


def test_a_corrupt_model_asset_fails_with_an_action_not_a_traceback():
    workspace = Workspace()
    try:
        with open(workspace.model_path, "w", encoding="utf-8") as handle:
            handle.write("not json at all")
        try:
            run(HANDLER.handle(workspace.sheets_context()))
        except InvalidJobError as exc:
            assert exc.action
            assert "JSON" in str(exc.detail) or "json" in str(exc.detail)
        else:  # pragma: no cover
            raise AssertionError("a corrupt asset must fail cleanly")
    finally:
        workspace.close()


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
def test_gltf_export_runs_here_end_to_end():
    """The one export with zero external requirements, so it is fully proven."""
    workspace = Workspace()
    try:
        ctx = workspace.export_context("gltf")
        result = run(HANDLER.handle(ctx))
        assert result.data["kind"] == "gltf"
        # The export's downloadUrl IS a URL: it is consumed immediately by the
        # export-job record, not persisted for tomorrow like a sheet artifact.
        assert result.data["downloadUrl"] == "https://example.invalid/export"
        with open(os.path.join(workspace.root, "export.bin"), "rb") as handle:
            data = handle.read()
        assert data[:4] == b"glTF"
        assert result.data["bytes"] == len(data)
        assert ctx.progress.percents()[-1] == 100
    finally:
        workspace.close()


def test_export_regenerates_the_sheets_it_needs():
    """An export must not require a prior sheets job. Determinism is what allows it."""
    workspace = Workspace()
    try:
        ctx = workspace.export_context("dxf")
        try:
            result = run(HANDLER.handle(ctx))
        except InvalidJobError as exc:
            # No ezdxf here: assert the honest, actionable refusal instead.
            assert "ezdxf" in str(exc.detail).lower()
            assert "PDF" in (exc.action or "") or "administrator" in (exc.action or "")
            return
        assert result.data["sheets"] >= 6
    finally:
        workspace.close()


def test_pdf_set_without_a_converter_says_what_to_install():
    workspace = Workspace()
    try:
        from services.drawings.export.pdf import find_converter

        ctx = workspace.export_context("pdf-set")
        if find_converter() is not None:
            result = run(HANDLER.handle(ctx))
            assert result.data["pages"] >= 6
            return
        try:
            run(HANDLER.handle(ctx))
        except InvalidJobError as exc:
            assert "PDF export isn't available" in str(exc)
            assert "DXF" in (exc.action or "")
        else:  # pragma: no cover
            raise AssertionError("no converter means no PDF")
    finally:
        workspace.close()


def test_an_unknown_export_kind_is_refused_before_any_work():
    workspace = Workspace()
    try:
        ctx = workspace.export_context("dwg")
        try:
            run(HANDLER.handle(ctx))
        except InvalidJobError as exc:
            assert "pdf-set" in str(exc.detail)
        else:  # pragma: no cover
            raise AssertionError("dwg is v1.1, not now")
        assert ctx.progress.events == [], "nothing should be reported for a rejected kind"
    finally:
        workspace.close()


def test_sheet_selection_narrows_the_export():
    workspace = Workspace()
    try:
        from services.drawings.export.pdf import find_converter

        if find_converter() is None:
            return  # nothing to select into; covered by the message test above
        ctx = workspace.export_context("pdf-set", sheetIds=["site-plan"])
        result = run(HANDLER.handle(ctx))
        assert result.data["pages"] == 1
    finally:
        workspace.close()


# ---------------------------------------------------------------------------
# Capability probe (what the boot log tells an operator)
# ---------------------------------------------------------------------------
def test_the_worker_reports_its_capabilities_honestly():
    from services.drawings.worker import probe_capabilities

    capabilities = probe_capabilities()
    assert capabilities["svg"] is True and capabilities["gltf"] is True
    try:
        import ezdxf  # noqa: F401

        assert capabilities["dxf"] is True
    except ImportError:
        assert capabilities["dxf"] is False
    assert capabilities["pdf"] == capabilities["png"]


def test_the_handler_envelope_keys_are_the_documented_ones():
    """Guard the contract the API's enqueue helper has to match."""
    assert ASSET_MODEL == "model"
    assert ASSET_AREAS == "areas"
    assert OUTPUT_EXPORT == "export"
    assert handler_module.EXPORT_KINDS == ("pdf-set", "dxf", "gltf", "png-pack")
    assert handler_module.DEFAULT_SHEET_FORMATS == ("svg", "dxf")


def _main() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            failed.append((name, "%s: %s" % (type(exc).__name__, exc)))
    print("\n%d passed, %d failed" % (passed, len(failed)))
    for name, message in failed:
        print("  FAIL %s — %s" % (name, message))
    timings = globals().get("_LAST_TIMINGS")
    if timings:
        print(
            "\n§14 sheet-set budget, %d sheets: load %d ms · draw %d ms · publish %d ms "
            "· total %d ms (limit 300 000 ms)"
            % (
                globals().get("_LAST_SHEETS", 0),
                timings["loadMs"],
                timings["drawMs"],
                timings["publishMs"],
                timings["totalMs"],
            )
        )
    from services.drawings.worker import probe_capabilities

    print("worker capabilities here: %s" % json.dumps(probe_capabilities(), sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
