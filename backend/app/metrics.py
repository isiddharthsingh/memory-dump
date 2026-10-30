"""Prometheus metrics. The API serves them at /metrics (all of its uvicorn
worker processes together, through PROMETHEUS_MULTIPROC_DIR); the arq worker
serves its own on WORKER_METRICS_PORT."""

from __future__ import annotations

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)

HTTP_REQUESTS = Counter("http_requests_total", "Requests answered", ["method", "route", "status"])
HTTP_SECONDS = Histogram("http_request_seconds", "Time to answer a request", ["method", "route"])
JOBS = Counter("jobs_total", "Jobs run, by how they ended", ["function", "outcome"])
JOB_SECONDS = Histogram(
    "job_seconds",
    "Time a job ran",
    ["function"],
    buckets=(0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)
QUEUE_JOBS = Gauge("queue_jobs", "Jobs queued, deferred ones included", multiprocess_mode="max")
QUEUE_OLDEST_SECONDS = Gauge(
    "queue_oldest_job_seconds",
    "How long the oldest job that is due has waited",
    multiprocess_mode="max",
)
PROVIDER_CALLS = Counter(
    "provider_calls_total", "Calls to Claude and Voyage", ["provider", "outcome"]
)
PROVIDER_TOKENS = Counter(
    "provider_tokens_total", "Tokens spent", ["provider", "model", "purpose", "kind"]
)


def render() -> tuple[bytes, str]:
    if "PROMETHEUS_MULTIPROC_DIR" in os.environ:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        return generate_latest(registry), CONTENT_TYPE_LATEST
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
