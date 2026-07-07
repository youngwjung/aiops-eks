import json
import os
import time
from datetime import datetime, timezone

import boto3
import httpx
import structlog
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel
from starlette.responses import Response

REVIEW_SERVICE_URL = os.environ.get("REVIEW_SERVICE_URL", "http://localhost:8004")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-5-haiku-20241022-v1:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317"
)
trace.set_tracer_provider(TracerProvider(resource=Resource.create({"service.name": "review-summary-service"})))
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True))
)
HTTPXClientInstrumentor().instrument()
BotocoreInstrumentor().instrument()

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger(service="review-summary-service")

app = FastAPI(title="review-summary-service")
FastAPIInstrumentor.instrument_app(app)

REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "path", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "path"]
)
BEDROCK_TOKENS = Counter(
    "bedrock_tokens_total", "Bedrock token usage", ["direction"]
)
BEDROCK_ERRORS = Counter(
    "bedrock_errors_total", "Bedrock invocation errors", ["error_code"]
)
BEDROCK_LATENCY = Histogram(
    "bedrock_invoke_duration_seconds", "Bedrock Converse API call latency"
)

_bedrock_client = None


def get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _bedrock_client


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration = time.time() - start
        path = request.url.path
        REQUEST_COUNT.labels(request.method, path, status_code).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(duration)
        log.info(
            "http_request",
            method=request.method,
            path=path,
            status=status_code,
            duration_ms=round(duration * 1000, 2),
        )


class ReviewSummaryBody(BaseModel):
    pros: list[str]
    cons: list[str]
    overall: str


class ReviewSummaryResponse(BaseModel):
    product_id: str
    review_count: int
    summary: ReviewSummaryBody
    generated_at: str


SYSTEM_PROMPT = (
    "당신은 이커머스 상품 리뷰를 분석하는 어시스턴트입니다. "
    "주어진 리뷰 목록을 바탕으로 장점(pros), 단점(cons), 총평(overall)을 정리하세요. "
    '다른 설명 없이 다음 형식의 JSON만 출력하세요: '
    '{"pros": ["...", "..."], "cons": ["...", "..."], "overall": "..."}'
)


async def _fetch_reviews(product_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(
                f"{REVIEW_SERVICE_URL}/api/reviews", params={"product_id": product_id}
            )
        except httpx.RequestError as exc:
            log.error("review_service_call_failed", product_id=product_id, error=str(exc))
            raise HTTPException(
                status_code=502, detail={"error": "review-service unavailable", "code": "UPSTREAM_ERROR"}
            )
    resp.raise_for_status()
    return resp.json()["items"]


def _invoke_bedrock(product_id: str, reviews: list[dict]) -> ReviewSummaryBody:
    review_text = "\n".join(f"- (평점 {r['rating']}/5) {r['comment']}" for r in reviews)
    client = get_bedrock_client()

    start = time.time()
    try:
        response = client.converse(
            modelId=BEDROCK_MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": f"상품 ID: {product_id}\n리뷰 목록:\n{review_text}"}]}],
            inferenceConfig={"maxTokens": 512, "temperature": 0.3},
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "Unknown")
        BEDROCK_ERRORS.labels(error_code).inc()
        log.error("bedrock_invoke_failed", product_id=product_id, error_code=error_code)

        if error_code == "ThrottlingException":
            raise HTTPException(status_code=429, detail={"error": "bedrock throttled", "code": "THROTTLED"})
        if error_code == "AccessDeniedException":
            raise HTTPException(
                status_code=403, detail={"error": "bedrock access denied", "code": "BEDROCK_ACCESS_DENIED"}
            )
        if error_code == "ValidationException":
            raise HTTPException(
                status_code=502, detail={"error": "invalid bedrock request (model id/region?)", "code": "BEDROCK_VALIDATION_ERROR"}
            )
        if error_code == "ResourceNotFoundException":
            raise HTTPException(
                status_code=502,
                detail={"error": "bedrock model not found in this region (BEDROCK_MODEL_ID?)", "code": "BEDROCK_MODEL_NOT_FOUND"},
            )
        raise HTTPException(status_code=502, detail={"error": "bedrock invocation failed", "code": "UPSTREAM_ERROR"})
    except BotoCoreError as exc:
        BEDROCK_ERRORS.labels("ClientSideError").inc()
        log.error("bedrock_invoke_failed", product_id=product_id, error=str(exc))
        raise HTTPException(
            status_code=502, detail={"error": "bedrock client error (credentials/region?)", "code": "BEDROCK_CLIENT_ERROR"}
        )
    finally:
        BEDROCK_LATENCY.observe(time.time() - start)

    usage = response.get("usage", {})
    BEDROCK_TOKENS.labels("input").inc(usage.get("inputTokens", 0))
    BEDROCK_TOKENS.labels("output").inc(usage.get("outputTokens", 0))

    output_text = response["output"]["message"]["content"][0]["text"]
    try:
        parsed = json.loads(output_text)
        return ReviewSummaryBody(**parsed)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        log.error("bedrock_response_parse_failed", product_id=product_id, raw=output_text, error=str(exc))
        raise HTTPException(
            status_code=502, detail={"error": "failed to parse bedrock response", "code": "BEDROCK_PARSE_ERROR"}
        )


@app.get("/api/review-summaries/{product_id}", response_model=ReviewSummaryResponse)
async def get_review_summary(product_id: str):
    reviews = await _fetch_reviews(product_id)
    if not reviews:
        raise HTTPException(status_code=404, detail={"error": "no reviews", "code": "NO_REVIEWS"})

    summary = _invoke_bedrock(product_id, reviews)

    return ReviewSummaryResponse(
        product_id=product_id,
        review_count=len(reviews),
        summary=summary,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
