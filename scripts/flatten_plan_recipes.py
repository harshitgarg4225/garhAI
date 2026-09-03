"""Flatten ``solver.apply_option`` wrappers in fixtures/plans/*.json into their expanded ops.

The sequencer stores an applied option as ONE op whose payload carries the server-built
expansion (storey/wall/opening/stair/room ops in dependency order). Replaying that
wrapper in a fresh project would look the solver job up again — in another firm — and
fail. A template recipe therefore carries the expansion itself. Idempotent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLANS = Path(__file__).resolve().parents[1] / "fixtures" / "plans"


def flatten(ops: list[dict]) -> tuple[list[dict], int]:
    out: list[dict] = []
    wrappers = 0
    for op in ops:
        if op.get("type") == "solver.apply_option":
            wrappers += 1
            for inner in op["payload"].get("ops") or []:
                out.append({"type": inner["type"], "payload": inner.get("payload") or {}})
        else:
            out.append({"type": op["type"], "payload": op.get("payload") or {}})
    return out, wrappers


def ensure_reg_profile(record: dict) -> bool:
    """Recipes captured before the seeder wrote the plot's reg profile get it here,
    right after the road op — the same payload PUT /plot mirrors into the log."""
    ops = record["ops"]
    if any(op.get("type") == "plot.set_reg_profile" for op in ops):
        return False
    city = record.get("cityPack")
    if not city:
        return False
    at = next((i for i, op in enumerate(ops) if op.get("type") == "plot.set_road"), None)
    profile = {"type": "plot.set_reg_profile", "payload": {"cityPack": city, "overrides": {}}}
    ops.insert((at + 1) if at is not None else 0, profile)
    return True


def main() -> int:
    for path in sorted(PLANS.glob("*.json")):
        record = json.loads(path.read_text())
        ops, wrappers = flatten(record["ops"])
        record["ops"] = ops
        added_profile = ensure_reg_profile(record)
        if wrappers or added_profile:
            path.write_text(json.dumps(record, indent=2) + "\n")
        if added_profile:
            print("  %-24s reg profile added (%s)" % (path.stem, record.get("cityPack")))
        print(
            "  %-24s %s → %d ops"
            % (
                path.stem,
                "flattened %d wrapper(s)" % wrappers if wrappers else "already flat",
                len(ops),
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
