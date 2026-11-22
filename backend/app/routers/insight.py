"""洞察归并专用接口：批量归并、规则下发、按人群应用、冲突裁决、刷新与追溯。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import service_for

router = APIRouter(prefix="/api/insight", tags=["洞察归并"])


class MergePayload(BaseModel):
    result_ids: list[int] = Field(default_factory=list)


class PublishPayload(BaseModel):
    merge_nos: list[str] | None = None


class ApplyPayload(BaseModel):
    subject: str


class ResolvePayload(BaseModel):
    subject: str
    rule_no: str
    reason: str
    operator: str = "analyst"


@router.post("/merge")
def merge_insights(payload: MergePayload) -> dict[str, Any]:
    service = service_for("mining_result")
    return service.merge_topic_insights(payload.result_ids)


@router.get("/merges")
def list_merges(topic: str | None = None) -> dict[str, Any]:
    service = service_for("mining_result")
    return service.list_merged_insights(topic)


@router.get("/receipts")
def list_receipts(result_ids: str | None = None) -> dict[str, Any]:
    service = service_for("mining_result")
    ids = [int(part) for part in result_ids.split(",") if part.strip()] if result_ids else None
    return service.get_merge_receipts(ids)


@router.post("/publish-rules")
def publish_rules(payload: PublishPayload) -> dict[str, Any]:
    service = service_for("scenario_label")
    return service.publish_merge_rules(payload.merge_nos)


@router.get("/rules")
def list_rules() -> dict[str, Any]:
    service = service_for("scenario_label")
    return service.list_published_rules()


@router.post("/apply")
def apply_rules(payload: ApplyPayload) -> dict[str, Any]:
    service = service_for("scenario_label")
    return service.apply_published_rules(payload.subject.strip())


@router.post("/resolve-conflict")
def resolve_conflict(payload: ResolvePayload) -> dict[str, Any]:
    service = service_for("scenario_label")
    return service.resolve_rule_conflict(
        payload.subject.strip(), payload.rule_no.strip(), payload.reason, payload.operator)


@router.post("/refresh-rules")
def refresh_rules() -> dict[str, Any]:
    service = service_for("scenario_label")
    return service.refresh_published_rules()


@router.get("/trace/{subject}")
def trace_subject(subject: str) -> dict[str, Any]:
    service = service_for("scenario_label")
    return service.trace_subject_disposal(subject)
