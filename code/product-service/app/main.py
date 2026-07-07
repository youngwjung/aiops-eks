import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger(service="product-service")

OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317"
)
trace.set_tracer_provider(TracerProvider(resource=Resource.create({"service.name": "product-service"})))
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True))
)

app = FastAPI(title="product-service")
FastAPIInstrumentor.instrument_app(app)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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


class ProductCreate(BaseModel):
    name: str
    description: str
    price: int = Field(gt=0)
    image_url: str
    stock: int = Field(ge=0)
    category: str


class Product(BaseModel):
    id: str
    name: str
    description: str
    price: int
    image_url: str
    stock: int
    category: str
    created_at: str


class ProductList(BaseModel):
    items: list[Product]
    total: int


PRODUCTS: dict[str, dict] = {}

SEED_PRODUCTS = [
    dict(
        id="earbuds-1",
        name="솔트사운드 무선 이어버드 Pro",
        description="액티브 노이즈 캔슬링과 30시간 배터리를 지원하는 완전 무선 이어버드입니다.",
        price=89000,
        stock=120,
        category="음향가전",
    ),
    dict(
        id="headphones-1",
        name="솔트사운드 오버이어 헤드폰 X1",
        description="몰입감 있는 사운드와 폭신한 이어패드로 장시간 착용에도 편안한 오버이어 헤드폰.",
        price=159000,
        stock=45,
        category="음향가전",
    ),
    dict(
        id="speaker-1",
        name="소금타워 블루투스 스피커",
        description="IPX7 방수를 지원하는 휴대용 블루투스 스피커. 캠핑이나 야외활동에 적합합니다.",
        price=69000,
        stock=80,
        category="음향가전",
    ),
    dict(
        id="smartwatch-1",
        name="솔트핏 스마트워치 S3",
        description="심박수, 수면, 운동량을 24시간 추적하는 올인원 스마트워치.",
        price=219000,
        stock=60,
        category="웨어러블",
    ),
    dict(
        id="fitnessband-1",
        name="솔트핏 슬림 밴드",
        description="가볍고 얇은 디자인의 데일리 피트니스 트래커.",
        price=49000,
        stock=150,
        category="웨어러블",
    ),
    dict(
        id="projector-1",
        name="소금빔 미니 프로젝터 M1",
        description="손바닥 크기로 어디서나 100인치 화면을 즐길 수 있는 미니 빔프로젝터.",
        price=189000,
        stock=30,
        category="영상가전",
    ),
    dict(
        id="charger-1",
        name="소금차지 무선 충전 패드",
        description="Qi 표준을 지원하는 15W 고속 무선 충전 패드.",
        price=29000,
        stock=200,
        category="액세서리",
    ),
    dict(
        id="mouse-1",
        name="솔트기어 게이밍 마우스 G5",
        description="16000 DPI 센서와 커스텀 사이드 버튼을 지원하는 게이밍 마우스.",
        price=59000,
        stock=90,
        category="PC주변기기",
    ),
    dict(
        id="keyboard-1",
        name="솔트기어 기계식 키보드 K7",
        description="청축 스위치를 채택한 텐키리스 기계식 키보드.",
        price=129000,
        stock=55,
        category="PC주변기기",
    ),
    dict(
        id="webcam-1",
        name="소금캠 풀HD 웹캠",
        description="1080p 화질과 자동 노출 보정을 지원하는 화상회의용 웹캠.",
        price=45000,
        stock=70,
        category="PC주변기기",
    ),
    dict(
        id="coffeemachine-1",
        name="소금브루 전자동 커피머신",
        description="원터치로 에스프레소부터 아메리카노까지 내려주는 전자동 커피머신.",
        price=349000,
        stock=20,
        category="생활가전",
    ),
    dict(
        id="airpurifier-1",
        name="소금에어 공기청정기 A9",
        description="미세먼지와 냄새를 동시에 잡아주는 헤파 필터 탑재 공기청정기.",
        price=259000,
        stock=35,
        category="생활가전",
    ),
]


@app.on_event("startup")
def seed_products():
    if PRODUCTS:
        return
    for item in SEED_PRODUCTS:
        PRODUCTS[item["id"]] = {
            **item,
            "image_url": f"/static/images/{item['id']}.jpg",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    log.info("seed_products_created", count=len(PRODUCTS))


@app.get("/api/products", response_model=ProductList)
def list_products(
    category: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    items = list(PRODUCTS.values())
    if category:
        items = [p for p in items if p["category"] == category]
    items.sort(key=lambda p: p["created_at"])
    total = len(items)
    start = (page - 1) * size
    page_items = items[start : start + size]
    return ProductList(items=[Product(**p) for p in page_items], total=total)


@app.get("/api/products/{product_id}", response_model=Product)
def get_product(product_id: str):
    product = PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail={"error": "product not found", "code": "NOT_FOUND"})
    return Product(**product)


@app.post("/api/products", response_model=Product, status_code=201)
def create_product(body: ProductCreate):
    product_id = str(uuid.uuid4())
    product = {
        "id": product_id,
        **body.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    PRODUCTS[product_id] = product
    return Product(**product)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
