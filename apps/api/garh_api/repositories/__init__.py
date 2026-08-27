"""Repository layer — the only code allowed to touch tables.

Import everything from this package rather than from the submodules; the flat surface
is the contract the router layer codes against.

Firm-scoped repositories — constructor is always ``(session, ctx)``::

    FirmRepository(session, ctx)                 # the caller's own firm
    UserRepository(session, ctx)                 # firm members
    ProjectRepository(session, ctx)
    PlotRepository(session, ctx)                 # one per project
    BriefRepository(session, ctx)                # one per project
    OpRepository(session, ctx)                   # append-only op log (409 on stale base)
    DesignVersionRepository(session, ctx)        # snapshots, named versions, options
    SolverJobRepository(session, ctx)
    RenderJobRepository(session, ctx)
    SheetRepository(session, ctx)
    AnnotationRepository(session, ctx)
    ComplianceReportRepository(session, ctx)
    ShareLinkRepository(session, ctx)
    CommentRepository(session, ctx)
    CreditEventRepository(session, ctx)
    AuditLogRepository(session, ctx)

Non-tenant repositories — constructor is ``(session)`` only, because no tenant context
can exist yet (pre-auth) or the data is global config. Each one is narrow by design;
see its module docstring for why it is safe::

    AuthDirectoryRepository(session)   # email → principal, signup
    OtpCodeRepository(session)         # email OTP challenges
    FlagRepository(session)            # global feature flags
    ShareTokenResolver(session)        # share token → firm/project/scope

Transactions: repositories ``flush()``, never ``commit()``. The request (or worker)
owns the unit of work — see :func:`garh_api.db.session_scope`.
"""

from __future__ import annotations

from garh_api.repositories.audit_log import AUDIT_ACTIONS, AuditLogRepository
from garh_api.repositories.auth_directory import AuthDirectoryRepository
from garh_api.repositories.briefs import BriefRepository, apply_merge_patch
from garh_api.repositories.comments import CommentRepository
from garh_api.repositories.compliance import ComplianceReportRepository
from garh_api.repositories.credits import CreditEventRepository
from garh_api.repositories.design_versions import (
    DesignVersionRepository,
    canonical_json,
    compute_snapshot_hash,
    snapshot_due,
)
from garh_api.repositories.domain import (
    Annotation,
    AuditEntry,
    AuthPrincipal,
    Brief,
    Comment,
    ComplianceReport,
    CreditEvent,
    DesignVersion,
    DesignVersionSummary,
    Firm,
    Flag,
    NewOp,
    Op,
    OpAppendResult,
    OtpChallenge,
    Plot,
    Project,
    RenderJob,
    ResolvedShare,
    ShareLink,
    Sheet,
    SolverJob,
    User,
)
from garh_api.repositories.firms import FirmRepository
from garh_api.repositories.flags import (
    DEFAULT_FLAGS,
    FLAG_REGISTRY,
    FlagRegistry,
    FlagRepository,
)
from garh_api.repositories.jobs import RenderJobRepository, SolverJobRepository
from garh_api.repositories.ops import EMPTY_BRANCH_HEAD, OpRepository
from garh_api.repositories.otp import (
    OtpCodeRepository,
    OtpVerification,
    generate_otp_code,
    hash_otp_code,
)
from garh_api.repositories.plots import PlotRepository
from garh_api.repositories.projects import ProjectPatch, ProjectRepository
from garh_api.repositories.share_links import (
    SHARE_SECTIONS,
    ShareLinkRepository,
    ShareTokenResolver,
    hash_share_token,
)
from garh_api.repositories.sheets import AnnotationRepository, SheetRepository
from garh_api.repositories.users import UserRepository, normalise_email

# Re-exported from tenancy so a route handler needs one import for the whole layer.
from garh_api.tenancy import (
    CrossTenantAccessError,
    EntityNotFoundError,
    InvalidCursorError,
    OpSequenceConflictError,
    Page,
    PermissionDeniedError,
    Repository,
    RepositoryUsageError,
    TenancyError,
    TenantContextRequiredError,
    TenantCtx,
    system_unscoped_session,
)

__all__ = [
    # repositories — firm-scoped
    "AnnotationRepository",
    "AuditLogRepository",
    "BriefRepository",
    "CommentRepository",
    "ComplianceReportRepository",
    "CreditEventRepository",
    "DesignVersionRepository",
    "FirmRepository",
    "OpRepository",
    "PlotRepository",
    "ProjectRepository",
    "RenderJobRepository",
    "ShareLinkRepository",
    "SheetRepository",
    "SolverJobRepository",
    "UserRepository",
    # repositories — non-tenant (documented exceptions)
    "AuthDirectoryRepository",
    "FlagRepository",
    "OtpCodeRepository",
    "ShareTokenResolver",
    # tenancy surface
    "CrossTenantAccessError",
    "EntityNotFoundError",
    "InvalidCursorError",
    "OpSequenceConflictError",
    "Page",
    "PermissionDeniedError",
    "Repository",
    "RepositoryUsageError",
    "TenancyError",
    "TenantContextRequiredError",
    "TenantCtx",
    "system_unscoped_session",
    # domain objects
    "Annotation",
    "AuditEntry",
    "AuthPrincipal",
    "Brief",
    "Comment",
    "ComplianceReport",
    "CreditEvent",
    "DesignVersion",
    "DesignVersionSummary",
    "Firm",
    "Flag",
    "NewOp",
    "Op",
    "OpAppendResult",
    "OtpChallenge",
    "OtpVerification",
    "Plot",
    "Project",
    "ProjectPatch",
    "RenderJob",
    "ResolvedShare",
    "Sheet",
    "ShareLink",
    "SolverJob",
    "User",
    # helpers & constants
    "AUDIT_ACTIONS",
    "DEFAULT_FLAGS",
    "EMPTY_BRANCH_HEAD",
    "FLAG_REGISTRY",
    "FlagRegistry",
    "SHARE_SECTIONS",
    "apply_merge_patch",
    "canonical_json",
    "compute_snapshot_hash",
    "generate_otp_code",
    "hash_otp_code",
    "hash_share_token",
    "normalise_email",
    "snapshot_due",
]
