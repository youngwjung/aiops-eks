import os
import time
from pathlib import Path
from typing import Optional

import httpx
import structlog
from fastapi import Cookie, FastAPI, Form, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

PRODUCT_SERVICE_URL = os.environ.get("PRODUCT_SERVICE_URL", "http://localhost:8002")
ORDER_SERVICE_URL = os.environ.get("ORDER_SERVICE_URL", "http://localhost:8003")
USER_SERVICE_URL = os.environ.get("USER_SERVICE_URL", "http://localhost:8001")
REVIEW_SERVICE_URL = os.environ.get("REVIEW_SERVICE_URL", "http://localhost:8004")
AI_REVIEW_SUMMARY_ENABLED = os.environ.get("AI_REVIEW_SUMMARY_ENABLED", "false").lower() == "true"
AI_REVIEW_SUMMARY_SERVICE_URL = os.environ.get("AI_REVIEW_SUMMARY_SERVICE_URL", "")

OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317"
)
trace.set_tracer_provider(TracerProvider(resource=Resource.create({"service.name": "frontend"})))
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True))
)
HTTPXClientInstrumentor().instrument()

BASE_DIR = Path(__file__).resolve().parent.parent

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger(service="frontend")

app = FastAPI(title="frontend")
FastAPIInstrumentor.instrument_app(app)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

REQUEST_COUNT = Counter("http_requests_total", "Total HTTP requests", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "path"])


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
            "http_request", method=request.method, path=path,
            status=status_code, duration_ms=round(duration * 1000, 2),
        )


def brand_context(**extra):
    ctx = {"brand_name": "소금가게", "ai_review_summary_enabled": AI_REVIEW_SUMMARY_ENABLED}
    ctx.update(extra)
    return ctx


@app.get("/")
async def index(request: Request, category: Optional[str] = None, access_token: Optional[str] = Cookie(None)):
    params = {"category": category} if category else {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{PRODUCT_SERVICE_URL}/api/products", params=params)
    resp.raise_for_status()
    data = resp.json()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            **brand_context(products=data["items"], category=category, logged_in=access_token is not None),
        },
    )


@app.get("/images/{filename}")
async def proxy_image(filename: str):
    async def stream():
        async with httpx.AsyncClient(timeout=10.0) as client:
            async with client.stream("GET", f"{PRODUCT_SERVICE_URL}/static/images/{filename}") as upstream:
                async for chunk in upstream.aiter_bytes():
                    yield chunk

    return StreamingResponse(stream(), media_type="image/jpeg")


@app.get("/products/{product_id}")
async def product_detail(request: Request, product_id: str, access_token: Optional[str] = Cookie(None)):
    async with httpx.AsyncClient(timeout=5.0) as client:
        product_resp = await client.get(f"{PRODUCT_SERVICE_URL}/api/products/{product_id}")
        if product_resp.status_code == 404:
            return templates.TemplateResponse(
                "not_found.html", {"request": request, **brand_context()}, status_code=404
            )
        product_resp.raise_for_status()
        product = product_resp.json()

        reviews_resp = await client.get(f"{REVIEW_SERVICE_URL}/api/reviews", params={"product_id": product_id})
        reviews_resp.raise_for_status()
        reviews = reviews_resp.json()["items"]

        summary = None
        summary_error = None
        if AI_REVIEW_SUMMARY_ENABLED and AI_REVIEW_SUMMARY_SERVICE_URL:
            try:
                summary_resp = await client.get(
                    f"{AI_REVIEW_SUMMARY_SERVICE_URL}/api/review-summaries/{product_id}"
                )
                if summary_resp.status_code == 200:
                    summary = summary_resp.json()["summary"]
                elif summary_resp.status_code == 404:
                    summary_error = "아직 리뷰가 충분하지 않아 요약을 만들 수 없습니다."
                else:
                    summary_error = "리뷰 요약을 불러오지 못했습니다."
            except httpx.RequestError as exc:
                log.error("review_summary_call_failed", product_id=product_id, error=str(exc))
                summary_error = "리뷰 요약 서비스에 연결할 수 없습니다."

    return templates.TemplateResponse(
        "product_detail.html",
        {
            "request": request,
            **brand_context(
                product=product,
                reviews=reviews,
                summary=summary,
                summary_error=summary_error,
                logged_in=access_token is not None,
            ),
        },
    )


@app.post("/products/{product_id}/reviews")
async def submit_review(
    product_id: str,
    rating: int = Form(...),
    comment: str = Form(...),
    access_token: Optional[str] = Cookie(None),
):
    if not access_token:
        return RedirectResponse(url="/login", status_code=303)
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(
            f"{REVIEW_SERVICE_URL}/api/reviews",
            json={"product_id": product_id, "rating": rating, "comment": comment},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    return RedirectResponse(url=f"/products/{product_id}", status_code=303)


@app.post("/products/{product_id}/order")
async def create_order(
    product_id: str,
    quantity: int = Form(1),
    access_token: Optional[str] = Cookie(None),
):
    if not access_token:
        return RedirectResponse(url="/login", status_code=303)
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(
            f"{ORDER_SERVICE_URL}/api/orders",
            json={"items": [{"product_id": product_id, "quantity": quantity}]},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    return RedirectResponse(url="/orders", status_code=303)


@app.get("/orders")
async def list_orders(request: Request, access_token: Optional[str] = Cookie(None)):
    if not access_token:
        return RedirectResponse(url="/login", status_code=303)
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{ORDER_SERVICE_URL}/api/orders", headers={"Authorization": f"Bearer {access_token}"}
        )
    resp.raise_for_status()
    return templates.TemplateResponse(
        "orders.html", {"request": request, **brand_context(orders=resp.json(), logged_in=True)}
    )


@app.get("/login")
async def login_form(request: Request, access_token: Optional[str] = Cookie(None)):
    return templates.TemplateResponse(
        "login.html", {"request": request, **brand_context(error=None, logged_in=access_token is not None)}
    )


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{USER_SERVICE_URL}/api/users/login", json={"email": email, "password": password}
        )
    if resp.status_code != 200:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, **brand_context(error="이메일 또는 비밀번호가 올바르지 않습니다.")},
            status_code=401,
        )
    token = resp.json()["access_token"]
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("access_token")
    return response


@app.get("/register")
async def register_form(request: Request, access_token: Optional[str] = Cookie(None)):
    return templates.TemplateResponse(
        "register.html", {"request": request, **brand_context(error=None, logged_in=access_token is not None)}
    )


@app.post("/register")
async def register(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form(...)):
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{USER_SERVICE_URL}/api/users/register",
            json={"email": email, "password": password, "name": name},
        )
    if resp.status_code != 201:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, **brand_context(error="회원가입에 실패했습니다. 이미 등록된 이메일일 수 있습니다.")},
            status_code=400,
        )
    return RedirectResponse(url="/login", status_code=303)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
