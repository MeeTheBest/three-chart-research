"""Anonymous expiring sessions, optionally backed by durable JSON snapshots."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from functools import wraps
from threading import RLock
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SYSTEMS = ("ziwei", "bazi", "vedic")
DEFAULT_TTL = timedelta(minutes=30)
FROZEN_RESULT_TTL = timedelta(days=1)


class SessionNotFound(KeyError):
    pass


@dataclass(frozen=True)
class BirthInput:
    date: str
    time: str
    place: str
    gender: str
    timezone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    bazi_time_standard: Literal["civil", "true_solar"] = "civil"

    def validate(self) -> None:
        try:
            datetime.strptime(self.date, "%Y-%m-%d")
            datetime.strptime(self.time, "%H:%M")
        except ValueError as exc:
            raise ValueError("date must be YYYY-MM-DD and time must be HH:MM") from exc
        if not self.place.strip():
            raise ValueError("place is required")
        if self.gender not in {"男", "女"}:
            raise ValueError("gender must be 男 or 女")
        if self.timezone:
            try:
                ZoneInfo(self.timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"unknown IANA timezone: {self.timezone}") from exc
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be supplied together")
        if self.bazi_time_standard not in {"civil", "true_solar"}:
            raise ValueError("bazi_time_standard must be civil or true_solar")
        if self.bazi_time_standard == "true_solar" and self.longitude is None:
            raise ValueError("longitude is required for true_solar")


@dataclass
class _Session:
    birth: BirthInput
    created_at: datetime
    expires_at: datetime
    analyses: dict[str, dict[str, Any]] = field(default_factory=dict)
    integration: dict[str, Any] | None = None
    raw_documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    analysis_progress: dict[str, dict[str, Any]] = field(default_factory=dict)
    diagnostics: dict[str, dict[str, Any]] = field(default_factory=dict)
    checkpoints: dict[str, dict[str, Any]] = field(default_factory=dict)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _locked(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return run


class EphemeralSessionStore:
    """Process-local temporary sessions for a single anonymous visitor.

    The store intentionally exposes only analysis identifiers to the comparison
    layer. It never generates a unified conclusion or action advice.
    """

    def __init__(self, *, ttl: timedelta = DEFAULT_TTL, frozen_result_ttl: timedelta = FROZEN_RESULT_TTL, repository=None) -> None:
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        if frozen_result_ttl <= timedelta(0):
            raise ValueError("frozen_result_ttl must be positive")
        self._ttl = ttl
        self._frozen_result_ttl = frozen_result_ttl
        self._sessions: dict[str, _Session] = {}
        self._lock = RLock()
        self.repository = repository

    @_locked
    def create(self, birth: BirthInput, *, now: datetime | None = None) -> str:
        birth.validate()
        created_at = now or _utc_now()
        if created_at.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        session_id = token_urlsafe(24)
        self._sessions[session_id] = _Session(
            birth=birth,
            created_at=created_at,
            expires_at=created_at + self._ttl,
        )
        self._save(session_id)
        return session_id

    @_locked
    def get_birth_input(self, session_id: str, *, now: datetime | None = None) -> BirthInput:
        return self._get(session_id, now=now).birth

    @_locked
    def set_analysis_progress(
        self, session_id: str, system: str, progress: dict[str, Any], *, now: datetime | None = None,
    ) -> None:
        if system not in (*SYSTEMS, "integration"):
            raise ValueError("unknown analysis system")
        session = self._get(session_id, now=now)
        self._renew_for_active_processing(session, now=now)
        session.analysis_progress[system] = deepcopy(progress)
        self._save(session_id)

    @_locked
    def get_analysis_progress(self, session_id: str, system: str, *, now: datetime | None = None) -> dict[str, Any]:
        if system not in (*SYSTEMS, "integration"):
            raise ValueError("unknown analysis system")
        session = self._get(session_id, now=now)
        return deepcopy(session.analysis_progress.get(system, {"state": "idle", "system": system}))

    @_locked
    def set_diagnostic(
        self, session_id: str, key: str, diagnostic: dict[str, Any], *, now: datetime | None = None,
    ) -> None:
        """Keep model replies and failed-validation details only for this session."""
        session = self._get(session_id, now=now)
        self._renew_for_active_processing(session, now=now)
        session.diagnostics[key] = deepcopy(diagnostic)
        self._save(session_id)

    @_locked
    def get_diagnostic(self, session_id: str, key: str, *, now: datetime | None = None) -> dict[str, Any]:
        session = self._get(session_id, now=now)
        return deepcopy(session.diagnostics.get(key, {"status": "none", "key": key, "events": []}))

    @_locked
    def record_single_analysis(
        self,
        session_id: str,
        system: str,
        analysis: dict[str, Any],
        *,
        raw_document: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        if system not in SYSTEMS:
            raise ValueError("unknown analysis system")
        if analysis.get("analysisType") != system:
            raise ValueError("analysisType must match the system being recorded")
        run = analysis.get("run", {})
        if run.get("answerKeyAccess") is not False or run.get("knownOutcomeAccess") is not False:
            raise ValueError("only answer-isolated pre-validation analyses may be stored")
        if analysis.get("validationVersion", {}).get("phase") != "pre-validation":
            raise ValueError("only pre-validation analyses may be stored")
        session = self._get(session_id, now=now)
        self._renew_for_frozen_result(session, now=now)
        session.analyses[system] = deepcopy(analysis)
        if raw_document is not None:
            document_id = raw_document.get("documentId")
            if not isinstance(document_id, str) or not document_id:
                raise ValueError("raw_document requires a non-empty documentId")
            session.raw_documents[document_id] = deepcopy(raw_document)
        self._save(session_id)

    @_locked
    def get_raw_documents(self, session_id: str, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
        """Return a copy for validation only; never expose it through the website API."""
        return deepcopy(self._get(session_id, now=now).raw_documents)

    @_locked
    def get_single_analyses(self, session_id: str, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
        """Return frozen single-system records without exposing raw documents."""
        return deepcopy(self._get(session_id, now=now).analyses)

    @_locked
    def record_integration_analysis(
        self, session_id: str, analysis: dict[str, Any], *, now: datetime | None = None,
    ) -> None:
        """Freeze a comparison result alongside its three already-frozen inputs."""
        if analysis.get("analysisType") != "integration":
            raise ValueError("analysisType must be integration")
        session = self._get(session_id, now=now)
        if any(system not in session.analyses for system in SYSTEMS):
            raise ValueError("all three single-system analyses are required before comparison")
        self._renew_for_frozen_result(session, now=now)
        session.integration = deepcopy(analysis)
        self._save(session_id)

    @_locked
    def get_integration_analysis(self, session_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        session = self._get(session_id, now=now)
        return deepcopy(session.integration)

    @_locked
    def session_summary(self, session_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Return history metadata only; birth data and raw documents stay private."""
        session = self._get(session_id, now=now)
        completed = tuple(system for system in SYSTEMS if system in session.analyses)
        return {
            "createdAt": session.created_at.isoformat(),
            "expiresAt": session.expires_at.isoformat(),
            "completedSystems": completed,
            "comparisonCompleted": session.integration is not None,
        }

    @_locked
    def comparison_status(self, session_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        session = self._get(session_id, now=now)
        completed = tuple(system for system in SYSTEMS if system in session.analyses)
        missing = tuple(system for system in SYSTEMS if system not in session.analyses)
        if missing:
            return {
                "status": "locked",
                "completedSystems": completed,
                "missingSystems": missing,
                "reason": "Complete all three single-system analyses before opening comparison.",
            }
        return {
            "status": "ready",
            "completedSystems": completed,
            "comparisonCompleted": session.integration is not None,
            "comparisonOnly": True,
            "analysisIds": {system: session.analyses[system]["analysisId"] for system in SYSTEMS},
            "allowedContent": ("agreements", "partial-agreements", "conflicts", "uncertainties", "evidence-grades"),
            "forbiddenContent": ("unified-conclusion", "unified-life-profile", "integrated-action-advice"),
        }

    @_locked
    def close(self, session_id: str) -> None:
        """Irreversibly remove all birth data and derived analyses for this session."""
        self._sessions.pop(session_id, None)
        if self.repository:
            self.repository.delete(session_id)

    @_locked
    def delete_expired(self, *, now: datetime | None = None) -> int:
        current = now or _utc_now()
        expired = [key for key, session in self._sessions.items() if current >= session.expires_at]
        for key in expired:
            self.close(key)
        return len(expired)

    @_locked
    def get_checkpoint(self, session_id: str, key: str) -> dict[str, Any]:
        return deepcopy(self._get(session_id, now=None).checkpoints.get(key, {}))

    @_locked
    def set_checkpoint(self, session_id: str, key: str, checkpoint: dict[str, Any]) -> None:
        session = self._get(session_id, now=None)
        self._renew_for_frozen_result(session, now=None)
        session.checkpoints[key] = deepcopy(checkpoint)
        self._save(session_id)

    @_locked
    def active_ids(self) -> list[str]:
        return self.repository.active_ids(_utc_now()) if self.repository else list(self._sessions)

    def _save(self, session_id: str) -> None:
        if self.repository:
            session = self._sessions[session_id]
            snapshot = asdict(session)
            for key in ("created_at", "expires_at"):
                snapshot[key] = snapshot[key].isoformat()
            self.repository.save(session_id, snapshot, session.expires_at)

    def _get(self, session_id: str, *, now: datetime | None) -> _Session:
        self.delete_expired(now=now)
        if session_id not in self._sessions and self.repository:
            snapshot = self.repository.load(session_id, now or _utc_now())
            if snapshot:
                snapshot["birth"] = BirthInput(**snapshot["birth"])
                for key in ("created_at", "expires_at"):
                    snapshot[key] = datetime.fromisoformat(snapshot[key])
                self._sessions[session_id] = _Session(**snapshot)
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFound("session does not exist, has expired, or has been closed") from exc

    def _renew_for_active_processing(self, session: _Session, *, now: datetime | None) -> None:
        """Keep an actively progressing long report alive without retaining idle data."""
        current = now or _utc_now()
        session.expires_at = max(session.expires_at, current + self._ttl)

    def _renew_for_frozen_result(self, session: _Session, *, now: datetime | None) -> None:
        """Keep frozen reports available for the user's requested one-day revisit window."""
        current = now or _utc_now()
        session.expires_at = max(session.expires_at, current + self._frozen_result_ttl)
