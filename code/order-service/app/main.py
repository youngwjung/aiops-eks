import os
import time
import uuid
from datetime import datetime, timezone
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
trace.set_tracer_provider(TracerProvider(resource=Resource.create({"service.name": "order-service"})))
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
log = structlog.get_logger(service="order-service")

app = FastAPI(title="order-service")
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


class OrderItemIn(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)


class OrderCreate(BaseModel):
    items: list[OrderItemIn]


class OrderItemOut(BaseModel):
    product_id: str
    name: str
    quantity: int
    unit_price: int


class Order(BaseModel):
    id: str
    user_id: str
    items: list[OrderItemOut]
    total_amount: int
    status: str
    created_at: str


ORDERS: dict[str, dict] = {}


def get_current_user_id(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing bearer token", "code": "UNAUTHORIZED"})
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail={"error": "invalid or expired token", "code": "UNAUTHORIZED"})
    return payload["sub"]


@app.post("/api/orders", response_model=Order, status_code=201)
async def create_order(body: OrderCreate, user_id: str = Depends(get_current_user_id)):
    if not body.items:
        raise HTTPException(status_code=400, detail={"error": "order must contain items", "code": "VALIDATION_ERROR"})

    order_items: list[OrderItemOut] = []
    total_amount = 0

    async with httpx.AsyncClient(timeout=5.0) as client:
        for item in body.items:
            try:
                resp = await client.get(f"{PRODUCT_SERVICE_URL}/api/products/{item.product_id}")
            except httpx.RequestError as exc:
                log.error("product_service_call_failed", product_id=item.product_id, error=str(exc))
                raise HTTPException(
                    status_code=502,
                    detail={"error": "product-service unavailable", "code": "UPSTREAM_ERROR"},
                )
            if resp.status_code == 404:
                raise HTTPException(
                    status_code=400,
                    detail={"error": f"product {item.product_id} not found", "code": "VALIDATION_ERROR"},
                )
            resp.raise_for_status()
            product = resp.json()
            unit_price = product["price"]
            order_items.append(
                OrderItemOut(
                    product_id=item.product_id,
                    name=product["name"],
                    quantity=item.quantity,
                    unit_price=unit_price,
                )
            )
            total_amount += unit_price * item.quantity

    order_id = str(uuid.uuid4())
    order = {
        "id": order_id,
        "user_id": user_id,
        "items": [i.model_dump() for i in order_items],
        "total_amount": total_amount,
        "status": "created",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    ORDERS[order_id] = order
    log.info("order_created", order_id=order_id, user_id=user_id, total_amount=total_amount)
    return Order(**order)


@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str, user_id: str = Depends(get_current_user_id)):
    order = ORDERS.get(order_id)
    if not order or order["user_id"] != user_id:
        raise HTTPException(status_code=404, detail={"error": "order not found", "code": "NOT_FOUND"})
    return Order(**order)


@app.get("/api/orders", response_model=list[Order])
def list_orders(user_id: str = Depends(get_current_user_id)):
    items = [Order(**o) for o in ORDERS.values() if o["user_id"] == user_id]
    items.sort(key=lambda o: o.created_at, reverse=True)
    return items


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
