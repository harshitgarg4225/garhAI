"""Where ``ScheduleRow`` and ``AreaStatementRow`` live — resolved, not copied.

These two dataclasses are the **shared** sheet primitives: the API persists them, the
renderers consume them, and this package produces them. There must be exactly one
definition of each, or a schedule row built here and a schedule row read by the sheet
composer would agree by coincidence rather than by type.

The single import is indirected through this module for one reason: Phase 8 is splitting
``services/drawings/sheets.py`` (a module) into ``services/drawings/sheets/`` (a
package, with the dataclasses in ``sheets/model.py``). Both shapes are correct; which
one is on disk depends on how far that split has landed. Resolving it in one place —
here — means the split cannot break this package, and there is still only one class.

If neither shape can be imported, that is a real breakage and it raises: silently
falling back to a local copy of the dataclass is precisely the "agree by coincidence"
failure this module exists to prevent.
"""

from __future__ import annotations

from typing import Any, Tuple

__all__ = ["AreaStatementRow", "ScheduleRow", "shared_primitive_origin"]


def _resolve() -> Tuple[Any, Any, str]:
    try:  # sheets.py as a module (today), or a package that re-exports them
        from services.drawings.sheets import AreaStatementRow as _Area
        from services.drawings.sheets import ScheduleRow as _Schedule

        return _Schedule, _Area, "services.drawings.sheets"
    except ImportError:
        pass
    try:  # sheets/ as a package whose __init__ does not re-export
        from services.drawings.sheets.model import AreaStatementRow as _Area
        from services.drawings.sheets.model import ScheduleRow as _Schedule

        return _Schedule, _Area, "services.drawings.sheets.model"
    except ImportError as error:
        raise ImportError(
            "Neither services.drawings.sheets nor services.drawings.sheets.model "
            "exposes ScheduleRow / AreaStatementRow. The schedules package renders "
            "those shared primitives and must not define its own copy — point this "
            "module at wherever the sheet model now lives. (%s)" % error
        ) from error


ScheduleRow, AreaStatementRow, SHARED_PRIMITIVE_MODULE = _resolve()


def shared_primitive_origin() -> str:
    """Which module the primitives came from — printed by the smoke run and asserted."""
    return SHARED_PRIMITIVE_MODULE
