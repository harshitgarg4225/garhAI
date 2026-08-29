"""The audit-action registry is the whole §13 audit trail, spelled as data.

Playbook §13: *"audit_log on auth events, exports, share creation, reg-profile
overrides, deletions."* Five categories. ``AuditLogRepository.record`` takes a free
string, deliberately — a repository that validated its own action vocabulary would
turn a typo into a 500 on a security-relevant write path, which is the worst
possible failure mode for an audit row. The vocabulary is therefore held as a
constant tuple that a reviewer greps and that these tests check.

Which means the constant only earns its keep if it is *complete*. It was not:
``auth.signup``, ``auth.logout`` and ``auth.refresh_reuse_detected`` were declared
privately inside :mod:`garh_api.auth` with a "fold these into ``AUDIT_ACTIONS``"
comment. A reviewer grepping the registry for the refresh-reuse action — the single
most important row in the table, because it means a stolen refresh token was
replayed — would have concluded it was not written at all.

No Postgres, no Redis: this is string algebra over module constants and the AST.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest
from garh_api.auth import AUTH_AUDIT_ACTIONS
from garh_api.repositories.audit_log import AUDIT_ACTIONS

API_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Actions whose WRITE SITE does not exist yet, with the phase that adds it.
#:
#: A constant in the registry with no caller reads, to anyone grepping, as "this is
#: audited". Four of them were not. The route that would emit them has not been built,
#: so the honest state is "declared, reserved, not yet emitted" — and the way to keep
#: that honest is to enumerate it here and have
#: :func:`test_declared_actions_are_either_emitted_or_listed_as_pending` fail in BOTH
#: directions: a new action with no caller and no entry here fails, and wiring one up
#: without deleting its entry here also fails.
#:
#: Deleting a line from this dict is part of the diff that adds the route.
PENDING_ACTIONS = {
    # Golden rule 5: "Architects can override anything; overrides are logged." The
    # override control is part of the compliance strip.
    "compliance.overridden": "Phase 2 (rules engine) / Phase 4 (compliance chips UI)",
    # UserRepository.set_role exists and is tested; no team-management route is
    # mounted, because member management is not on the MVP route surface (§11).
    #
    # ``user.removed`` used to sit here beside it. It no longer does: F-6's DPDP
    # erasure route (``POST /privacy/erasure``) removes the seat and emits the row,
    # so the entry would now be the stale "not implemented yet" note this dict's own
    # docstring warns about.
    "user.role_changed": "Phase 9 (team management surface)",
    # FirmRepository.merge_settings / .replace_settings exist; no firm-settings route.
    "firm.settings_changed": "Phase 9 (firm settings surface)",
}

#: One action per §13 category, so the test names the requirement it defends.
REQUIRED_BY_SECTION_13 = {
    "auth events": (
        "auth.otp_requested",
        "auth.otp_verified",
        "auth.otp_failed",
        "auth.signup",
        "auth.logout",
        "auth.logout_all",
        "auth.token_refreshed",
        "auth.refresh_reuse_detected",
    ),
    "exports": ("export.created", "export.downloaded"),
    "share creation": ("share.created", "share.revoked"),
    "reg-profile overrides": ("reg_profile.overridden", "compliance.overridden"),
    "deletions": ("project.deleted", "project.archived", "user.removed"),
}


def test_no_duplicate_actions() -> None:
    assert len(AUDIT_ACTIONS) == len(set(AUDIT_ACTIONS)), (
        "AUDIT_ACTIONS has a duplicate; the tuple is read as a set everywhere, so a "
        "duplicate is a copy-paste that hid a missing action."
    )


@pytest.mark.parametrize("category", sorted(REQUIRED_BY_SECTION_13))
def test_section_13_categories_are_all_represented(category: str) -> None:
    missing = sorted(set(REQUIRED_BY_SECTION_13[category]) - set(AUDIT_ACTIONS))
    assert not missing, (
        "§13 requires an audit row for %s, and %s is/are not in AUDIT_ACTIONS. "
        "Add the constant there (not at the call site) so a security review can find "
        "it by grepping one file." % (category, missing)
    )


def test_auth_module_actions_are_all_in_the_registry() -> None:
    """``garh_api.auth`` must not carry a private action vocabulary.

    This is the regression guard for the three actions that used to live only in
    ``auth.py``. If a future auth feature needs a new action, the constant goes in
    ``repositories/audit_log.py`` and ``auth.py`` imports it.
    """
    stragglers = sorted(set(AUTH_AUDIT_ACTIONS) - set(AUDIT_ACTIONS))
    assert not stragglers, (
        "garh_api.auth emits audit action(s) %s that AUDIT_ACTIONS does not declare. "
        "Move the constant into garh_api/repositories/audit_log.py and import it." % stragglers
    )


def test_auth_module_defines_no_action_string_of_its_own() -> None:
    """Stronger than the previous test: no ``= "auth.xxx"`` literal in auth.py.

    The set-difference test above passes if a local constant merely *happens* to
    equal a registry entry, which is exactly the state that drifts. This one fails
    on the shape.
    """
    path = os.path.join(API_ROOT, "garh_api", "auth.py")
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)

    offenders: list[str] = []
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        else:
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        # An audit action, as opposed to any other string constant, is
        # `<entity>.<verb>` with no spaces — the shape record() stores.
        text = value.value
        if targets and targets[0].startswith("ACTION_") and "." in text:
            offenders.append("%s = %r" % (targets[0], text))

    assert not offenders, (
        "garh_api/auth.py declares audit action literal(s) %s. The canonical list is "
        "garh_api/repositories/audit_log.py — declare it there and import it, so there "
        "is one spelling of every action string." % offenders
    )


def _constant_names_by_action() -> dict[str, str]:
    """``{"share.created": "ACTION_SHARE_CREATED", ...}`` read out of the registry.

    Read from the AST rather than derived from the action string: the mapping is not
    mechanical. ``ACTION_AUTH_REFRESH_REUSE`` holds ``"auth.refresh_reuse_detected"``,
    so a ``"ACTION_" + action.replace(".", "_").upper()`` rule reports the single most
    security-relevant action in the table as un-emitted. Guessing the name here would
    have made this test cry wolf on the one row that must never be missing.
    """
    path = os.path.join(API_ROOT, "garh_api", "repositories", "audit_log.py")
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        else:
            continue
        if not isinstance(target, ast.Name) or not target.id.startswith("ACTION_"):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            out[node.value.value] = target.id
    return out


def _modules_referencing(name: str) -> list[str]:
    """Every module under ``garh_api/`` that mentions ``name``, minus the registry.

    A textual scan rather than an import graph: the point is what a reviewer grepping
    the tree would find, and a constant referenced only inside its own defining module
    is exactly the case this is looking for.
    """
    root = os.path.join(API_ROOT, "garh_api")
    registry = os.path.join(root, "repositories", "audit_log.py")
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            if os.path.abspath(path) == os.path.abspath(registry):
                continue
            with open(path, encoding="utf-8") as handle:
                if name in handle.read():
                    found.append(os.path.relpath(path, root))
    return sorted(found)


def test_declared_actions_are_either_emitted_or_listed_as_pending() -> None:
    """Every registry action has a write site, or an entry in :data:`PENDING_ACTIONS`.

    §13 lists five categories of audited event. Declaring the constant is the cheap
    half; emitting the row is the half that matters, and the two drifted — four
    constants had no caller anywhere in ``garh_api/``, which makes the registry read
    as a promise the code does not keep.
    """
    names = _constant_names_by_action()
    unnamed = sorted(set(AUDIT_ACTIONS) - set(names))
    assert not unnamed, (
        "Action(s) %s are in AUDIT_ACTIONS but no ACTION_* constant in "
        "repositories/audit_log.py holds that string. Call sites import the constant, "
        "so an action with no constant cannot be emitted." % unnamed
    )

    unemitted = {action for action in AUDIT_ACTIONS if not _modules_referencing(names[action])}

    undocumented = sorted(unemitted - set(PENDING_ACTIONS))
    assert not undocumented, (
        "Audit action(s) %s are declared in AUDIT_ACTIONS but no module under "
        "garh_api/ references their ACTION_* constant, so no row is ever written. "
        "Either emit them at the write path, or add them to PENDING_ACTIONS with the "
        "phase that will — an unaudited action that looks audited is worse than an "
        "absent one." % undocumented
    )

    stale = sorted(set(PENDING_ACTIONS) - unemitted)
    assert not stale, (
        "PENDING_ACTIONS still lists %s, but %s now has a write site. Delete the "
        "entry — a stale 'not implemented yet' note is how a real gap hides."
        % (stale, "it" if len(stale) == 1 else "they")
    )


def test_pending_actions_name_a_real_phase() -> None:
    """The excuse must say which phase, so it can be chased."""
    for action, phase in PENDING_ACTIONS.items():
        assert action in AUDIT_ACTIONS, (
            "%r is in PENDING_ACTIONS but not in AUDIT_ACTIONS — remove it." % action
        )
        assert phase.startswith("Phase "), (
            "PENDING_ACTIONS[%r] = %r must name the phase that implements it." % (action, phase)
        )


def test_registry_actions_all_look_like_actions() -> None:
    for action in AUDIT_ACTIONS:
        assert action == action.strip(), "%r has surrounding whitespace" % action
        assert "." in action, (
            "%r is not <entity>.<verb>; the trail is filtered by prefix in "
            "AuditLogRepository.list_recent(action=...)" % action
        )
        assert action == action.lower(), "%r should be lower_snake with dots" % action
