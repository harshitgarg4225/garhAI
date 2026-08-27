"""StabilityRenderProvider wire-format and error-mapping tests. Hermetic — no network.

Everything runs against a STRICT ``httpx.MockTransport`` double: the handler rejects a
wrong endpoint path, a wrong ``authorization`` header and a missing ``accept: image/*``
before it ever returns an image. Per CLAUDE.md's negative-test rule, the suite then
*proves those rejections can fire* by deliberately breaking the endpoint and the key —
a strict double nobody has ever seen refuse is indistinguishable from a lenient one.

What is pinned here, and what is not:

* pinned — the endpoint path, the auth/accept headers, the multipart body (control
  image bytes, prompt text, control_strength per §9's mode numbers, seed), the
  status-code → worker-error mapping, the CONTENT_FILTERED refusal, and that the
  result is fitted to the requested frame;
* NOT pinned — anything about what the live API actually paints. That needs a real
  key and eyes on the output (the ``__main__`` smoke path in the provider module),
  and it is a launch gate, not a unit test.
"""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image

from services.common.config import WorkerSettings
from services.common.errors import ProviderError
from services.render.imaging import encode_png
from services.render.provider import PROVIDER_NAMES, get_render_provider
from services.render.stability_provider import (
    MAX_API_PIXELS,
    STRUCTURE_PATH,
    StabilityRenderProvider,
    _upload_resolution,
)
from services.render.types import RenderRequest

API_KEY = "test-key-123"


# ---------------------------------------------------------------------------
# fixtures and the strict double
# ---------------------------------------------------------------------------
def _settings(**overrides: object) -> WorkerSettings:
    values: dict[str, object] = {
        "provider_render": "stability",
        "stability_api_key": API_KEY,
    }
    values.update(overrides)
    return WorkerSettings(_env_file=None, **values)  # type: ignore[call-arg]


def _png(
    width: int = 320, height: int = 200, color: tuple[int, int, int] = (188, 178, 166)
) -> bytes:
    return encode_png(Image.new("RGB", (width, height), color))


def _request(mode: str = "explore", **overrides: object) -> RenderRequest:
    values: dict[str, object] = {
        "viewport_png": _png(),
        "mode": mode,
        "preset": "exterior-street-day",
        "seed": 7,
        "size": (512, 288),
    }
    if mode == "precise":
        values["depth_png"] = _png(color=(90, 90, 90))
    values.update(overrides)
    return RenderRequest(**values)  # type: ignore[arg-type]


def _strict_handler(recorded: list[httpx.Request], respond=None):
    """A double that VERIFIES the request before answering.

    The endpoint/auth/accept checks are the assertions under negative test below: if
    the provider ever drifts on any of them, the happy-path tests fail loudly here
    instead of at the first live call.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if request.url.path != STRUCTURE_PATH:
            return httpx.Response(404, json={"errors": ["wrong endpoint: %s" % request.url.path]})
        if request.headers.get("authorization") != "Bearer %s" % API_KEY:
            return httpx.Response(401, json={"errors": ["invalid api key"]})
        if "image/*" not in request.headers.get("accept", ""):
            return httpx.Response(406, json={"errors": ["accept must be image/*"]})
        if respond is not None:
            return respond(request)
        return httpx.Response(
            200,
            content=_png(64, 40, (120, 140, 180)),
            headers={"finish-reason": "SUCCESS", "seed": "7"},
        )

    return handler


def _provider(recorded: list[httpx.Request], respond=None, **settings_overrides: object):
    return StabilityRenderProvider(
        _settings(**settings_overrides),
        transport=httpx.MockTransport(_strict_handler(recorded, respond)),
    )


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------
def test_happy_path_returns_a_real_render_result() -> None:
    recorded: list[httpx.Request] = []
    result = _provider(recorded).render(_request())

    assert result.provider == "stability"
    assert result.is_mock is False, "a hosted render must never be badged as a mock"
    assert result.safety_flagged is False
    assert (result.width, result.height) == (512, 288)
    image = Image.open(io.BytesIO(result.image_png))
    assert image.size == (512, 288), "the 64x40 API answer must be fitted to the request"
    assert result.duration_ms >= 0
    assert result.metadata["endpoint"] == "control/structure"
    assert len(recorded) == 1


def test_multipart_body_carries_the_control_image_and_the_prompt() -> None:
    recorded: list[httpx.Request] = []
    _provider(recorded).render(_request())

    request = recorded[0]
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.read()
    # The control image travels as real PNG bytes under the `image` field.
    assert b'name="image"' in body
    assert b"\x89PNG\r\n\x1a\n" in body, "the composited viewport must be in the body"
    # The prompt is the preset template — this fragment is load-bearing product copy.
    assert b'name="prompt"' in body
    assert b"Indian residential house" in body
    assert b'name="negative_prompt"' in body
    assert b'name="seed"' in body and b"\r\n7\r\n" in body
    assert b'name="output_format"' in body and b"png" in body


@pytest.mark.parametrize(
    ("mode", "expected_strength"),
    [("explore", b"0.35"), ("precise", b"0.90")],
    ids=["explore-0.35", "precise-0.90"],
)
def test_mode_maps_to_the_section_9_control_strength(mode: str, expected_strength: bytes) -> None:
    """Precise/Explore reuse MODE_PARAMS' 0.9/0.35 — one source for the §9 promise."""
    recorded: list[httpx.Request] = []
    _provider(recorded).render(_request(mode=mode))

    body = recorded[0].read()
    assert b'name="control_strength"' in body
    assert expected_strength in body


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def test_factory_selects_stability_when_a_key_is_present() -> None:
    provider = get_render_provider(_settings())
    assert isinstance(provider, StabilityRenderProvider)
    assert provider.name == "stability"
    assert "stability" in PROVIDER_NAMES


def test_factory_refuses_stability_without_a_key() -> None:
    """Same loud-config-error convention as PROVIDER_LLM=anthropic without a key."""
    with pytest.raises(ValueError) as excinfo:
        get_render_provider(_settings(stability_api_key=""))
    message = str(excinfo.value)
    assert "PROVIDER_RENDER=stability" in message
    assert "STABILITY_API_KEY" in message
    assert "mock" in message, "the error must name the zero-key escape hatch"


# ---------------------------------------------------------------------------
# error mapping
# ---------------------------------------------------------------------------
def _respond_with(status: int, body: dict[str, object] | None = None):
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body or {"errors": ["synthetic"]})

    return respond


def test_401_maps_to_a_permanent_error_naming_the_env_var() -> None:
    provider = _provider([], respond=_respond_with(401))
    with pytest.raises(ProviderError) as excinfo:
        provider.render(_request())
    error = excinfo.value
    assert error.retryable is False, "retrying cannot fix a bad key"
    assert "STABILITY_API_KEY" in (error.detail or "")
    assert "401" not in error.message, "status codes are operator detail, not user copy"


@pytest.mark.parametrize("status", [402, 429, 500, 503])
def test_credits_rate_limits_and_5xx_are_retryable(status: int) -> None:
    provider = _provider([], respond=_respond_with(status))
    with pytest.raises(ProviderError) as excinfo:
        provider.render(_request())
    assert excinfo.value.retryable is True
    assert excinfo.value.status == status


@pytest.mark.parametrize("status", [400, 413, 422])
def test_other_4xx_are_permanent(status: int) -> None:
    provider = _provider([], respond=_respond_with(status))
    with pytest.raises(ProviderError) as excinfo:
        provider.render(_request())
    assert excinfo.value.retryable is False


def test_a_content_filtered_200_is_refused_not_stored() -> None:
    """Stability signals moderation as a 200 + blurred image; storing it would lie."""

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=_png(64, 40), headers={"finish-reason": "CONTENT_FILTERED"}
        )

    provider = _provider([], respond=respond)
    with pytest.raises(ProviderError) as excinfo:
        provider.render(_request())
    assert excinfo.value.code == "render_safety_blocked"
    assert excinfo.value.retryable is False


def test_a_timeout_is_retryable() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated", request=request)

    provider = _provider([], respond=respond)
    with pytest.raises(ProviderError) as excinfo:
        provider.render(_request())
    assert excinfo.value.retryable is True


# ---------------------------------------------------------------------------
# negative controls (CLAUDE.md: a gate that cannot go red is worse than no gate)
# ---------------------------------------------------------------------------
def test_negative_control_a_wrong_endpoint_is_rejected_by_the_double() -> None:
    """Prove the double's path check can fire.

    A base_url with a path prefix shifts the request off STRUCTURE_PATH; if the
    double answered it anyway, every endpoint assertion above would be decorative.
    """
    recorded: list[httpx.Request] = []
    provider = _provider(recorded, stability_base_url="https://api.stability.ai/broken")
    with pytest.raises(ProviderError):
        provider.render(_request())
    assert recorded[0].url.path != STRUCTURE_PATH


def test_negative_control_a_wrong_key_is_rejected_by_the_double() -> None:
    """Prove the double's authorization check can fire (and maps like a real 401)."""
    provider = _provider([], stability_api_key="wrong-key")
    with pytest.raises(ProviderError) as excinfo:
        provider.render(_request())
    assert excinfo.value.retryable is False
    assert "STABILITY_API_KEY" in (excinfo.value.detail or "")


# ---------------------------------------------------------------------------
# sizing
# ---------------------------------------------------------------------------
def test_upload_resolution_respects_the_api_pixel_cap() -> None:
    width, height = _upload_resolution(8192, 8192)
    assert width * height <= MAX_API_PIXELS
    assert width == height, "aspect must be preserved"
    # Under the cap nothing changes — the API answers at the exact requested frame.
    assert _upload_resolution(2048, 1152) == (2048, 1152)


def test_validation_still_runs_before_any_network_call() -> None:
    """precise without a depth map must fail locally — no request may be recorded."""
    recorded: list[httpx.Request] = []
    provider = _provider(recorded)
    with pytest.raises(ValueError):
        provider.render(_request(mode="precise", depth_png=None))
    assert recorded == [], "an invalid request must never reach the wire"
