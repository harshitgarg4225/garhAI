"""Import-time stand-ins for worker dependencies that are absent locally.

Why this exists
---------------
``services.common`` imports ``structlog`` and ``pydantic`` at module scope, and
``services/solver/__init__.py`` re-exports through it. That makes every solver
module — including the deliberately ortools-free ones that were *designed* to be
runnable on a bare interpreter — unimportable on a machine with no dependencies
installed. See the toolchain-gap row in ``DECISIONS.md``.

The stubs cover exactly the surface touched on the import path and nothing more.
A real package always wins: a stub is installed only when the import fails, so
this is inert in CI, in Docker, and on any developer machine that ran
``pip install``.

This is a *local verification* aid, not a runtime shim. Nothing in the request
path may depend on it, and it must never be imported by production code — only
by tests (via ``services/solver/tests/conftest.py``) and by scripts under
``scripts/``.
"""

from __future__ import annotations

import sys
import types


def _install_structlog() -> bool:
    """Install a no-op structlog. Returns True when a stub was installed."""
    try:
        import structlog  # noqa: F401

        return False
    except ImportError:
        pass

    stub = types.ModuleType("structlog")

    class _Logger:
        def _noop(self, *args: object, **kwargs: object) -> None:
            return None

        info = warning = debug = error = exception = critical = msg = _noop

        def bind(self, **kwargs: object) -> "_Logger":
            return self

        def unbind(self, *args: object) -> "_Logger":
            return self

        def new(self, **kwargs: object) -> "_Logger":
            return self

    stub.get_logger = lambda *a, **k: _Logger()  # type: ignore[attr-defined]
    stub.BoundLogger = _Logger  # type: ignore[attr-defined]
    stub.configure = lambda *a, **k: None  # type: ignore[attr-defined]

    contextvars_mod = types.ModuleType("structlog.contextvars")
    contextvars_mod.bind_contextvars = lambda **k: None  # type: ignore[attr-defined]
    contextvars_mod.clear_contextvars = lambda: None  # type: ignore[attr-defined]
    contextvars_mod.unbind_contextvars = lambda *a: None  # type: ignore[attr-defined]
    stub.contextvars = contextvars_mod  # type: ignore[attr-defined]

    stdlib_mod = types.ModuleType("structlog.stdlib")
    stdlib_mod.BoundLogger = _Logger  # type: ignore[attr-defined]
    stub.stdlib = stdlib_mod  # type: ignore[attr-defined]

    processors_mod = types.ModuleType("structlog.processors")
    for _name in ("JSONRenderer", "TimeStamper", "add_log_level", "format_exc_info"):
        setattr(processors_mod, _name, lambda *a, **k: None)
    stub.processors = processors_mod  # type: ignore[attr-defined]

    sys.modules["structlog"] = stub
    sys.modules["structlog.contextvars"] = contextvars_mod
    sys.modules["structlog.stdlib"] = stdlib_mod
    sys.modules["structlog.processors"] = processors_mod
    return True


def _install_pydantic() -> bool:
    """Install a minimal pydantic/pydantic_settings. True when stubbed."""
    try:
        import pydantic  # noqa: F401

        return False
    except ImportError:
        pass

    pyd = types.ModuleType("pydantic")

    def _field(default: object = None, **kwargs: object) -> object:
        if default is None and "default_factory" in kwargs:
            factory = kwargs["default_factory"]
            return factory() if callable(factory) else None
        return default

    def _passthrough_decorator(*args: object, **kwargs: object):
        def deco(fn):
            return fn

        return deco

    class _BaseModel:
        """Enough of BaseModel for import-time class bodies to evaluate."""

        def __init__(self, **data: object) -> None:
            for key, value in data.items():
                setattr(self, key, value)

        @classmethod
        def model_validate(cls, data: object) -> "_BaseModel":
            return cls(**data) if isinstance(data, dict) else cls()

        def model_dump(self, **kwargs: object) -> dict:
            return dict(self.__dict__)

    class _AliasChoices:
        """Records the alias list; nothing here resolves aliases."""

        def __init__(self, *choices: str) -> None:
            self.choices = choices

    class _SecretStr(str):
        def get_secret_value(self) -> str:
            return str(self)

    pyd.Field = _field  # type: ignore[attr-defined]
    pyd.BaseModel = _BaseModel  # type: ignore[attr-defined]
    pyd.ConfigDict = dict  # type: ignore[attr-defined]
    pyd.AliasChoices = _AliasChoices  # type: ignore[attr-defined]
    pyd.SecretStr = _SecretStr  # type: ignore[attr-defined]
    pyd.AnyUrl = str  # type: ignore[attr-defined]
    pyd.AnyHttpUrl = str  # type: ignore[attr-defined]
    pyd.PostgresDsn = str  # type: ignore[attr-defined]
    pyd.RedisDsn = str  # type: ignore[attr-defined]
    pyd.EmailStr = str  # type: ignore[attr-defined]
    pyd.StrictInt = int  # type: ignore[attr-defined]
    pyd.StrictStr = str  # type: ignore[attr-defined]
    pyd.StrictBool = bool  # type: ignore[attr-defined]
    pyd.PrivateAttr = _field  # type: ignore[attr-defined]
    pyd.ValidationInfo = object  # type: ignore[attr-defined]
    pyd.field_validator = _passthrough_decorator  # type: ignore[attr-defined]
    pyd.model_validator = _passthrough_decorator  # type: ignore[attr-defined]
    pyd.computed_field = _passthrough_decorator  # type: ignore[attr-defined]
    pyd.field_serializer = _passthrough_decorator  # type: ignore[attr-defined]
    pyd.ValidationError = type("ValidationError", (ValueError,), {})  # type: ignore[attr-defined]
    sys.modules["pydantic"] = pyd

    pyds = types.ModuleType("pydantic_settings")

    class _BaseSettings(_BaseModel):
        pass

    pyds.BaseSettings = _BaseSettings  # type: ignore[attr-defined]
    pyds.SettingsConfigDict = dict  # type: ignore[attr-defined]
    sys.modules["pydantic_settings"] = pyds
    return True


def install_worker_dep_stubs() -> tuple[str, ...]:
    """Install stubs for any missing worker dependency.

    Returns the names actually stubbed, so callers can report honestly which
    packages were faked rather than silently implying a full environment.
    """
    stubbed: list[str] = []
    if _install_structlog():
        stubbed.append("structlog")
    if _install_pydantic():
        stubbed.append("pydantic")
    return tuple(stubbed)


__all__ = ["install_worker_dep_stubs"]
