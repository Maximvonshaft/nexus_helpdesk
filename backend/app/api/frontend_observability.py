from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..models import User
from ..services.observability import record_frontend_api_latency, record_web_vital
from .deps import get_current_user

router = APIRouter(prefix="/api/observability", tags=["frontend-observability"])

_UUID_SEGMENT = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_DYNAMIC_SEGMENT = re.compile(r"^(?:\d+|wc_[A-Za-z0-9_-]+|voice-[A-Za-z0-9_-]+)$")
_HEX_SEGMENT = re.compile(r"^[0-9a-f]{16,64}$", re.IGNORECASE)


class FrontendMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["web_vital", "api_latency"]
    name: Literal["LCP", "CLS", "INP"] | None = None
    rating: Literal["good", "needs-improvement", "poor"] | None = None
    value: float | None = Field(default=None, ge=0, le=120_000)
    path: str | None = Field(default=None, min_length=1, max_length=240)
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"] | None = None
    status: str | None = Field(default=None, min_length=1, max_length=32)
    duration_ms: float | None = Field(default=None, ge=0, le=120_000)


class FrontendMetricBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[FrontendMetric] = Field(min_length=1, max_length=50)


def _normalized_metric_path(path: str) -> str:
    segments = []
    for segment in path.split("/"):
        if not segment:
            continue
        decoded = segment.strip()
        if _UUID_SEGMENT.fullmatch(decoded) or _DYNAMIC_SEGMENT.fullmatch(decoded) or _HEX_SEGMENT.fullmatch(decoded):
            segments.append(":id")
        else:
            segments.append(decoded[:64])
    return "/" + "/".join(segments)


def _record_metric(metric: FrontendMetric) -> None:
    if metric.kind == "web_vital":
        if metric.name is None or metric.rating is None or metric.value is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="frontend_web_vital_fields_required",
            )
        if metric.name == "CLS" and metric.value > 10:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="frontend_cls_value_invalid",
            )
        # Browser Performance APIs report LCP and INP in milliseconds. The
        # canonical histogram uses seconds so its shared buckets remain useful
        # for LCP/INP while preserving CLS as its unitless score.
        normalized_value = metric.value / 1000 if metric.name in {"LCP", "INP"} else metric.value
        record_web_vital(metric.name, metric.rating, normalized_value)
        return

    if metric.path is None or metric.method is None or metric.status is None or metric.duration_ms is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="frontend_api_latency_fields_required",
        )
    if "?" in metric.path or "#" in metric.path or not metric.path.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="frontend_metric_path_invalid",
        )
    record_frontend_api_latency(
        _normalized_metric_path(metric.path),
        metric.method,
        metric.status,
        metric.duration_ms,
    )


@router.post("/frontend-metrics", status_code=status.HTTP_204_NO_CONTENT)
def submit_frontend_metrics(
    request: FrontendMetricBatch,
    _current_user: User = Depends(get_current_user),
) -> Response:
    for metric in request.metrics:
        _record_metric(metric)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
