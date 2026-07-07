import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import jwt
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
PRODUCT_SERVICE_URL = os.environ.get("PRODUCT_SERVICE_URL", "http://localhost:8002")

OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317"
)
trace.set_tracer_provider(TracerProvider(resource=Resource.create({"service.name": "review-service"})))
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True))
)
HTTPXClientInstrumentor().instrument()

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger(service="review-service")

app = FastAPI(title="review-service")
FastAPIInstrumentor.instrument_app(app)

REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "path", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "path"]
)


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


class ReviewCreate(BaseModel):
    product_id: str
    rating: int = Field(ge=1, le=5)
    comment: str


class Review(BaseModel):
    id: str
    product_id: str
    user_id: str
    user_name: str
    rating: int
    comment: str
    created_at: str


class ReviewList(BaseModel):
    items: list[Review]
    total: int


REVIEWS: dict[str, dict] = {}


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing bearer token", "code": "UNAUTHORIZED"})
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail={"error": "invalid or expired token", "code": "UNAUTHORIZED"})
    return {"id": payload["sub"], "name": payload.get("name", "익명")}


def _seed_review(product_id: str, user_name: str, rating: int, comment: str, days_ago: int) -> None:
    review_id = str(uuid.uuid4())
    REVIEWS[review_id] = {
        "id": review_id,
        "product_id": product_id,
        "user_id": f"seed-{uuid.uuid4().hex[:8]}",
        "user_name": user_name,
        "rating": rating,
        "comment": comment,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
    }


@app.on_event("startup")
def seed_reviews():
    if REVIEWS:
        return

    _seed_review("earbuds-1", "김소금", 5, "배터리가 정말 오래 가요. 한 번 충전하면 이틀은 거뜬합니다.", 12)
    _seed_review("earbuds-1", "이바다", 4, "노이즈 캔슬링이 이 가격대에 훌륭합니다. 다만 케이스가 살짝 헐렁한 느낌이에요.", 10)
    _seed_review("earbuds-1", "박짠맛", 2, "가끔 왼쪽 이어폰이 블루투스 연결이 끊기는 현상이 있어요. 재연결하면 되긴 하는데 불편합니다.", 8)
    _seed_review("earbuds-1", "정갯벌", 5, "통화 품질이 좋아서 재택근무할 때 회의용으로 잘 쓰고 있습니다.", 6)
    _seed_review("earbuds-1", "최미네랄", 3, "착용감은 편한데 저음이 약간 부족한 것 같아요. 음악 듣기엔 무난한 정도.", 4)
    _seed_review("earbuds-1", "한염전", 5, "가성비 최고입니다. 주변 지인들에게도 추천했어요.", 2)

    _seed_review("headphones-1", "김소금", 5, "이어패드가 정말 푹신해서 몇 시간을 써도 귀가 안 아파요.", 15)
    _seed_review("headphones-1", "이바다", 4, "사운드는 만족스러운데 헤드밴드 부분이 조금 뻑뻑한 느낌입니다.", 9)
    _seed_review("headphones-1", "박짠맛", 2, "일주일 만에 오른쪽에서 지지직거리는 잡음이 나기 시작했어요.", 7)
    _seed_review("headphones-1", "정갯벌", 4, "디자인이 예쁘고 색상도 마음에 듭니다.", 3)

    _seed_review("speaker-1", "김소금", 5, "방수 기능이 확실해서 물놀이할 때도 걱정 없이 씁니다.", 11)
    _seed_review("speaker-1", "이바다", 3, "소리는 좋은데 최대 음량에서 약간 찢어지는 느낌이 있어요.", 5)
    _seed_review("speaker-1", "박짠맛", 5, "휴대성이 좋고 배터리도 오래갑니다. 캠핑용으로 딱이에요.", 1)

    _seed_review("smartwatch-1", "정갯벌", 4, "운동량 측정이 정확한 편이고 화면도 밝아서 야외에서 잘 보입니다.", 14)
    _seed_review("smartwatch-1", "최미네랄", 2, "수면 추적 정확도가 기대에 못 미쳐요. 자꾸 뒤척임을 기상으로 인식합니다.", 8)
    _seed_review("smartwatch-1", "한염전", 5, "배터리가 이틀 넘게 가서 만족스럽습니다.", 4)
    _seed_review("smartwatch-1", "김소금", 3, "알림은 잘 오는데 앱 연동이 가끔 느려요.", 2)

    _seed_review("keyboard-1", "이바다", 5, "청축 타건감이 훌륭하고 타이핑할 때 스트레스가 풀립니다.", 9)
    _seed_review("keyboard-1", "박짠맛", 3, "소리가 큰 편이라 사무실에서 쓰기엔 눈치 보입니다.", 6)
    _seed_review("keyboard-1", "정갯벌", 4, "키압이 적당하고 인식 오류 없이 잘 작동합니다.", 2)

    _seed_review("coffeemachine-1", "최미네랄", 5, "원터치로 에스프레소가 나와서 아침마다 편하게 씁니다.", 13)
    _seed_review("coffeemachine-1", "한염전", 2, "세척할 때 분해가 번거롭고 부품이 많아 관리가 어려워요.", 7)
    _seed_review("coffeemachine-1", "김소금", 4, "커피 맛은 만족스러운데 소음이 생각보다 있는 편입니다.", 3)

    _seed_review("airpurifier-1", "이바다", 5, "미세먼지 수치가 확실히 빨리 떨어지는 게 눈에 보입니다.", 10)
    _seed_review("airpurifier-1", "박짠맛", 4, "필터 교체 알림이 있어서 편한데 필터 가격이 좀 비쌉니다.", 5)

    log.info("seed_reviews_created", count=len(REVIEWS))


async def _validate_product_exists(product_id: str) -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{PRODUCT_SERVICE_URL}/api/products/{product_id}")
        except httpx.RequestError as exc:
            log.error("product_service_call_failed", product_id=product_id, error=str(exc))
            raise HTTPException(
                status_code=502, detail={"error": "product-service unavailable", "code": "UPSTREAM_ERROR"}
            )
    if resp.status_code == 404:
        raise HTTPException(
            status_code=400, detail={"error": f"product {product_id} not found", "code": "VALIDATION_ERROR"}
        )
    resp.raise_for_status()


@app.post("/api/reviews", response_model=Review, status_code=201)
async def create_review(body: ReviewCreate, current_user: dict = Depends(get_current_user)):
    await _validate_product_exists(body.product_id)

    review_id = str(uuid.uuid4())
    review = {
        "id": review_id,
        "product_id": body.product_id,
        "user_id": current_user["id"],
        "user_name": current_user["name"],
        "rating": body.rating,
        "comment": body.comment,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    REVIEWS[review_id] = review
    log.info("review_created", review_id=review_id, product_id=body.product_id)
    return Review(**review)


@app.get("/api/reviews", response_model=ReviewList)
def list_reviews(product_id: str):
    items = [Review(**r) for r in REVIEWS.values() if r["product_id"] == product_id]
    items.sort(key=lambda r: r.created_at, reverse=True)
    return ReviewList(items=items, total=len(items))


@app.get("/api/reviews/{review_id}", response_model=Review)
def get_review(review_id: str):
    review = REVIEWS.get(review_id)
    if not review:
        raise HTTPException(status_code=404, detail={"error": "review not found", "code": "NOT_FOUND"})
    return Review(**review)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
