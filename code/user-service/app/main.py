import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, EmailStr, Field
from starlette.responses import Response

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "60"))

OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://k8s-monitoring-trace-alloy-receiver.tracing.svc.cluster.local:4317"
)
trace.set_tracer_provider(TracerProvider(resource=Resource.create({"service.name": "user-service"})))
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True))
)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger(service="user-service")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


app = FastAPI(title="user-service")
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


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    created_at: str


class UserPublic(BaseModel):
    id: str
    name: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


USERS_BY_ID: dict[str, dict] = {}
USERS_BY_EMAIL: dict[str, str] = {}


def _create_user(email: str, password: str, name: str) -> dict:
    user_id = str(uuid.uuid4())
    user = {
        "id": user_id,
        "email": email,
        "name": name,
        "password_hash": hash_password(password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    USERS_BY_ID[user_id] = user
    USERS_BY_EMAIL[email] = user_id
    return user


def _issue_token(user: dict) -> str:
    payload = {
        "sub": user["id"],
        "email": user["email"],
        "name": user["name"],
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _to_user_out(user: dict) -> UserOut:
    return UserOut(
        id=user["id"], email=user["email"], name=user["name"], created_at=user["created_at"]
    )


@app.on_event("startup")
def seed_users():
    seed = [
        ("alice@saltmart.demo", "password123", "김소금"),
        ("bob@saltmart.demo", "password123", "이바다"),
        ("carol@saltmart.demo", "password123", "박짠맛"),
    ]
    for email, password, name in seed:
        if email not in USERS_BY_EMAIL:
            _create_user(email, password, name)
    log.info("seed_users_created", count=len(USERS_BY_EMAIL))


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing bearer token", "code": "UNAUTHORIZED"})
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail={"error": "invalid or expired token", "code": "UNAUTHORIZED"})
    user = USERS_BY_ID.get(payload.get("sub"))
    if not user:
        raise HTTPException(status_code=401, detail={"error": "user not found", "code": "UNAUTHORIZED"})
    return user


@app.post("/api/users/register", response_model=UserOut, status_code=201)
def register(body: UserCreate):
    if body.email in USERS_BY_EMAIL:
        raise HTTPException(status_code=400, detail={"error": "email already registered", "code": "VALIDATION_ERROR"})
    user = _create_user(body.email, body.password, body.name)
    return _to_user_out(user)


@app.post("/api/users/login", response_model=TokenResponse)
def login(body: UserLogin):
    user_id = USERS_BY_EMAIL.get(body.email)
    user = USERS_BY_ID.get(user_id) if user_id else None
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail={"error": "invalid credentials", "code": "UNAUTHORIZED"})
    token = _issue_token(user)
    return TokenResponse(access_token=token, user=_to_user_out(user))


@app.get("/api/users/me", response_model=UserOut)
def me(current_user: dict = Depends(get_current_user)):
    return _to_user_out(current_user)


@app.get("/api/users/{user_id}", response_model=UserPublic)
def get_public_user(user_id: str):
    user = USERS_BY_ID.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail={"error": "user not found", "code": "NOT_FOUND"})
    return UserPublic(id=user["id"], name=user["name"])


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
