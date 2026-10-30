"""Small API-facing wrapper around transactional paper quota operations."""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Mapping

from fastapi import HTTPException, Request

from paper_library.governance import (
    QuotaExceeded,
    ReservationHandle,
    confirm_output_artifact,
    record_audit,
    release_reservation,
    reserve_papers,
)

from .paper_audit import paper_identity_snapshot


class GovernedPaperOutput:
    def __init__(
        self,
        actor: Mapping[str, Any],
        paper_ids: Iterable[int],
        *,
        action: str,
        request: Request | None = None,
    ) -> None:
        self.actor = actor
        self.paper_ids = list(dict.fromkeys(int(item) for item in paper_ids))
        self.action = action
        self.request = request
        self.completed = False
        self.paper_snapshot = paper_identity_snapshot(self.paper_ids)
        idempotency_key = request.headers.get("idempotency-key") if request else None
        try:
            self.handle: ReservationHandle = reserve_papers(
                actor,
                self.paper_ids,
                action=action,
                reservation_id=idempotency_key,
            )
        except QuotaExceeded as exc:
            record_audit(
                actor,
                action=action,
                resource_type="paper",
                resource_count=len(self.paper_ids),
                result="denied",
                details={**exc.detail(), **self.paper_snapshot},
                **self._request_meta(),
            )
            if request:
                request.state.governance_audited = True
            raise HTTPException(status_code=429, detail=exc.detail()) from exc
        if self.handle.enforced:
            record_audit(
                actor,
                action="governance.quota_reserve",
                resource_type="quota_reservation",
                resource_id=self.handle.reservation_id,
                resource_count=len(self.handle.new_paper_ids),
                result="success",
                details={
                    "output_action": action,
                    "new_paper_ids": self.handle.new_paper_ids,
                    **self.paper_snapshot,
                },
                **self._request_meta(),
            )

    def _request_meta(self) -> dict[str, str | None]:
        if not self.request:
            return {"request_id": None, "ip_address": None, "user_agent": None}
        return {
            "request_id": getattr(self.request.state, "request_id", None),
            "ip_address": self.request.client.host if self.request.client else None,
            "user_agent": self.request.headers.get("user-agent"),
        }

    def complete(self, temp_info: Mapping[str, Any], *, details: Mapping[str, Any] | None = None) -> str:
        expires_at = dt.datetime.fromtimestamp(int(temp_info["expires_at"]), tz=dt.UTC)
        artifact_id = confirm_output_artifact(
            self.handle,
            self.actor,
            action=self.action,
            filename=str(temp_info["filename"]),
            content_type=temp_info.get("content_type"),
            file_id=str(temp_info["file_id"]),
            paper_ids=self.paper_ids,
            expires_at=expires_at,
        )
        if self.handle.enforced:
            record_audit(
                self.actor,
                action="governance.quota_confirm",
                resource_type="quota_reservation",
                resource_id=self.handle.reservation_id,
                resource_count=len(self.handle.new_paper_ids),
                result="success",
                details={"artifact_id": artifact_id, "output_action": self.action},
                **self._request_meta(),
            )
        self.completed = True
        audit_details = {
            "artifact_id": artifact_id,
            "filename": temp_info["filename"],
            "file_id": temp_info["file_id"],
            **(details or {}),
            **self.paper_snapshot,
        }
        record_audit(
            self.actor,
            action=self.action,
            resource_type="paper",
            resource_id=str(self.paper_ids[0]) if len(self.paper_ids) == 1 else None,
            resource_count=len(self.paper_ids),
            result="success",
            details=audit_details,
            **self._request_meta(),
        )
        if self.request:
            self.request.state.governance_audited = True
        return artifact_id

    def fail(self, exc: BaseException) -> None:
        if self.completed:
            return
        release_reservation(self.handle, error_code=type(exc).__name__)
        if self.handle.enforced:
            record_audit(
                self.actor,
                action="governance.quota_release",
                resource_type="quota_reservation",
                resource_id=self.handle.reservation_id,
                resource_count=len(self.handle.new_paper_ids),
                result="success",
                details={"reason": type(exc).__name__, "output_action": self.action},
                **self._request_meta(),
            )
        record_audit(
            self.actor,
            action=self.action,
            resource_type="paper",
            resource_count=len(self.paper_ids),
            result="failed",
            details={
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                **self.paper_snapshot,
            },
            **self._request_meta(),
        )
        if self.request:
            self.request.state.governance_audited = True

    def __enter__(self) -> "GovernedPaperOutput":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if not self.completed:
            self.fail(exc or RuntimeError("output generation exited before completion"))
        return False
