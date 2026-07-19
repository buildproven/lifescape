"""Local browser workspace for guided retirement comparisons."""

from __future__ import annotations

import csv
import io
import shutil
import threading
import time
import webbrowser
from collections import OrderedDict, deque
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal, TypedDict
from urllib.parse import urlsplit
from uuid import uuid4

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from lifescape.config import load_metrics
from lifescape.evidence import validate_unique_headers
from lifescape.models import GateState, RunResult
from lifescape.pipeline import execute_run
from lifescape.resources import bundled_benchmark

MAX_EVIDENCE_BYTES = 5_000_000
HOSTED_RUN_LIMIT = 6
HOSTED_RUN_WINDOW_SECONDS = 60.0
HOSTED_GLOBAL_RUN_LIMIT = 30
HOSTED_MAX_CONCURRENT_RUNS = 2
HOSTED_MAX_TRACKED_CLIENTS = 128
DOWNLOAD_FILES = frozenset(
    {"comparison.md", "comparison.csv", "sensitivity.csv", "lifescape.sqlite"}
)


class HostedRunGuard:
    """Bound hosted compute per process and provide an incident kill switch."""

    def __init__(
        self,
        *,
        enabled: bool,
        run_limit: int = HOSTED_RUN_LIMIT,
        window_seconds: float = HOSTED_RUN_WINDOW_SECONDS,
        global_run_limit: int = HOSTED_GLOBAL_RUN_LIMIT,
        max_concurrent: int = HOSTED_MAX_CONCURRENT_RUNS,
        max_tracked_clients: int = HOSTED_MAX_TRACKED_CLIENTS,
    ) -> None:
        self.enabled = enabled
        self.run_limit = run_limit
        self.window_seconds = window_seconds
        self.global_run_limit = global_run_limit
        self.max_concurrent = max_concurrent
        self.max_tracked_clients = max_tracked_clients
        self._active = 0
        self._global_requests: deque[float] = deque()
        self._requests: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def acquire(self, client_key: str) -> None:
        if not self.enabled:
            raise HTTPException(
                status_code=503,
                detail="hosted comparisons are temporarily disabled",
                headers={"Retry-After": "60"},
            )
        now = time.monotonic()
        with self._lock:
            cutoff = now - self.window_seconds
            while self._global_requests and self._global_requests[0] <= cutoff:
                self._global_requests.popleft()
            stale_clients = [
                key
                for key, history in self._requests.items()
                if not history or history[-1] <= cutoff
            ]
            for key in stale_clients:
                del self._requests[key]
            if self._active >= self.max_concurrent:
                raise HTTPException(
                    status_code=429,
                    detail="hosted comparison capacity is busy; try again shortly",
                    headers={"Retry-After": "5"},
                )
            if len(self._global_requests) >= self.global_run_limit:
                retry_after = max(
                    1,
                    ceil(self.window_seconds - (now - self._global_requests[0])),
                )
                raise HTTPException(
                    status_code=429,
                    detail="hosted comparison capacity is exhausted; try again shortly",
                    headers={"Retry-After": str(retry_after)},
                )
            requests = self._requests.get(client_key)
            if requests is None:
                if len(self._requests) >= self.max_tracked_clients:
                    raise HTTPException(
                        status_code=429,
                        detail="hosted comparison capacity is exhausted; try again shortly",
                        headers={"Retry-After": "60"},
                    )
                requests = deque()
                self._requests[client_key] = requests
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self.run_limit:
                retry_after = max(1, ceil(self.window_seconds - (now - requests[0])))
                raise HTTPException(
                    status_code=429,
                    detail="hosted comparison limit reached; try again shortly",
                    headers={"Retry-After": str(retry_after)},
                )
            requests.append(now)
            self._global_requests.append(now)
            self._active += 1

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)

    @property
    def tracked_client_count(self) -> int:
        with self._lock:
            return len(self._requests)


class BodyLimitMiddleware:
    """Reject oversized evidence requests before JSON parsing or endpoint dispatch."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_EVIDENCE_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != "/api/evidence/inspect"
        ):
            await self.app(scope, receive, send)
            return
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                response = JSONResponse(
                    {"detail": "request exceeds the 5 MB local-app import limit"},
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            more_body = message.get("more_body", False)

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


class HostedStaticBoundaryMiddleware:
    """Keep the public site outside the local application's API surface."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and (
            scope["path"].startswith("/api/") or scope["path"] == "/openapi.json"
        ):
            response = JSONResponse(
                {"detail": "the hosted site has no application API"},
                status_code=404,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class WebModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppRunRequest(WebModel):
    selected_place_ids: tuple[str, ...] = Field(min_length=2)
    purchase_budget_max: float = Field(ge=50_000, le=100_000_000)
    future_self_age: int = Field(ge=40, le=110)
    household: Literal["solo", "couple", "family"] = "couple"
    evidence_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{12}$")

    @field_validator("selected_place_ids")
    @classmethod
    def places_are_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("selected places must be unique")
        return values


class CatalogPlace(TypedDict):
    place_id: str
    name: str
    state: str
    complete_metrics: int
    total_metrics: int


def _read_evidence(
    csv_text: str, metric_ids: tuple[str, ...]
) -> tuple[list[str], list[dict[str, str]]]:
    _validate_csv_quoting(csv_text)
    try:
        reader = csv.DictReader(io.StringIO(csv_text), strict=True)
        if reader.fieldnames is None:
            raise ValueError("evidence CSV has no header")
        validate_unique_headers(reader.fieldnames)
        required = {"place_id", "place_name", "state", *metric_ids}
        missing = sorted(required - set(reader.fieldnames))
        if missing:
            raise ValueError(f"evidence CSV is missing columns: {missing}")
        rows: list[dict[str, str]] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"evidence CSV row {row_number} has extra columns")
            missing_values = sorted(name for name, value in row.items() if value is None)
            if missing_values:
                raise ValueError(
                    f"evidence CSV row {row_number} is missing values for: {missing_values}"
                )
            rows.append({name: value for name, value in row.items() if value is not None})
    except csv.Error as exc:
        raise ValueError(f"evidence CSV is malformed: {exc}") from exc
    if not rows:
        raise ValueError("evidence CSV has no rows")
    return list(reader.fieldnames), rows


def _validate_csv_quoting(csv_text: str) -> None:
    """Enforce RFC-style quote placement before Python's permissive CSV parser."""
    in_quotes = False
    at_field_start = True
    after_closing_quote = False
    index = 0
    while index < len(csv_text):
        character = csv_text[index]
        if in_quotes:
            if character == '"':
                if index + 1 < len(csv_text) and csv_text[index + 1] == '"':
                    index += 2
                    continue
                in_quotes = False
                after_closing_quote = True
            index += 1
            continue
        if after_closing_quote:
            if character == "," or character in "\r\n":
                at_field_start = True
                after_closing_quote = False
            else:
                raise ValueError(
                    "evidence CSV is malformed: unexpected character after closing quote"
                )
            index += 1
            continue
        if character == '"':
            if not at_field_start:
                raise ValueError("evidence CSV is malformed: quote inside an unquoted field")
            in_quotes = True
            at_field_start = False
        elif character == "," or character in "\r\n":
            at_field_start = True
        else:
            at_field_start = False
        index += 1
    if in_quotes:
        raise ValueError("evidence CSV is malformed: unterminated quoted field")


def _catalog(rows: list[dict[str, str]], metric_ids: tuple[str, ...]) -> list[CatalogPlace]:
    places: dict[str, CatalogPlace] = {}
    for row in rows:
        place_id = row["place_id"].strip()
        if not place_id:
            raise ValueError("evidence CSV contains a blank place_id")
        complete = sum(bool(row.get(metric_id, "").strip()) for metric_id in metric_ids)
        current = places.get(place_id)
        if current is None:
            places[place_id] = {
                "place_id": place_id,
                "name": row["place_name"].strip(),
                "state": row["state"].strip().upper(),
                "complete_metrics": complete,
                "total_metrics": len(metric_ids),
            }
        else:
            current["complete_metrics"] = min(
                len(metric_ids), current["complete_metrics"] + complete
            )
    return sorted(places.values(), key=lambda place: (place["state"], place["name"]))


def _evidence_kind(rows: list[dict[str, str]]) -> Literal["real", "synthetic", "mixed"]:
    flags = {row.get("synthetic", "").strip().lower() for row in rows}
    if flags == {"true"}:
        return "synthetic"
    if flags == {"false"}:
        return "real"
    return "mixed"


def _filter_evidence(
    fieldnames: list[str], rows: list[dict[str, str]], selected: set[str], destination: Path
) -> None:
    filtered = [row for row in rows if row["place_id"] in selected]
    found = {row["place_id"] for row in filtered}
    if found != selected:
        raise ValueError(f"unknown selected places: {sorted(selected - found)}")
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(filtered)


def _prepare_profile(
    source: Path,
    destination: Path,
    *,
    budget: float,
    age: int,
    household: str,
) -> None:
    profile = yaml.safe_load(source.read_text(encoding="utf-8"))
    profile["profile_version"] = "local-app-v1"
    profile["purchase_budget_max"] = budget
    profile["purchase_budget_min"] = min(float(profile["purchase_budget_min"]), budget)
    profile["future_self_ages"] = sorted({age, min(age + 10, 110), min(age + 20, 110)})
    profile["household"] = household
    destination.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")


def _response(run: RunResult, token: str, *, downloads_enabled: bool = True) -> dict[str, object]:
    places = {place.place_id: place for place in run.places}
    sensitivity = {item.place_id: item for item in run.sensitivity}
    gates_by_place: dict[str, list[dict[str, object]]] = {place_id: [] for place_id in places}
    for gate in run.gate_results:
        gates_by_place[gate.place_id].append(
            {
                "name": gate.gate_id.replace("_", " ").title(),
                "state": gate.result,
                "value": gate.raw_value,
                "threshold": gate.threshold,
                "notes": gate.notes,
            }
        )
    rankings = []
    for score in run.scores:
        place = places[score.place_id]
        stability = sensitivity[score.place_id]
        rankings.append(
            {
                "place_id": score.place_id,
                "name": place.name,
                "state": place.state,
                "rank": score.rank,
                "score": round(score.total_score, 1),
                "top_three_frequency": round(stability.top_three_frequency * 100),
                "fragile": stability.fragile,
                "criteria": [
                    {
                        "name": criterion.criterion.replace("_", " ").title(),
                        "score": round(criterion.normalized_score, 1),
                        "contribution": round(criterion.weighted_score, 2),
                    }
                    for criterion in sorted(
                        score.criteria, key=lambda item: item.weighted_score, reverse=True
                    )
                ],
                "gates": gates_by_place[score.place_id],
            }
        )
    ranked_ids = {item["place_id"] for item in rankings}
    blocked = [
        {
            "place_id": place_id,
            "name": place.name,
            "state": place.state,
            "gates": [
                gate
                for gate in gates_by_place[place_id]
                if gate["state"] in {GateState.FAIL, GateState.UNKNOWN}
            ],
        }
        for place_id, place in places.items()
        if place_id not in ranked_ids
    ]
    evidence_kind = _observation_evidence_kind(run)
    return {
        "run_id": run.run_id,
        "evaluated_as_of": run.evaluated_as_of.isoformat(),
        "evidence_through": run.evidence_through.date().isoformat(),
        "evidence_kind": evidence_kind,
        "has_synthetic": evidence_kind != "real",
        "rankings": rankings,
        "blocked": blocked,
        "downloads": (
            {name: f"/api/downloads/{token}/{name}" for name in sorted(DOWNLOAD_FILES)}
            if downloads_enabled
            else {}
        ),
    }


def _observation_evidence_kind(run: RunResult) -> Literal["real", "synthetic", "mixed"]:
    flags = {observation.source.synthetic for observation in run.observations}
    if flags == {True}:
        return "synthetic"
    if flags == {False}:
        return "real"
    return "mixed"


def _validate_mutation_origin(
    request: Request,
    *,
    trust_forwarded_proto: bool = False,
    require_origin: bool = False,
) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        if require_origin:
            raise HTTPException(status_code=403, detail="request origin is required")
        return
    parsed = urlsplit(origin)
    effective_scheme = request.url.scheme
    if trust_forwarded_proto:
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        if forwarded_proto in {"http", "https"}:
            effective_scheme = forwarded_proto
    if parsed.scheme != effective_scheme or parsed.netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="request origin is not this local app")


def _hosted_client_key(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded_for:
        return forwarded_for
    if request.client is not None:
        return request.client.host
    return "unknown"


def create_app(
    output_dir: Path | None = None,
    *,
    hosted_demo: bool = False,
    hosted_runs_enabled: bool | None = None,
    hosted_run_limit: int = HOSTED_RUN_LIMIT,
    hosted_run_window_seconds: float = HOSTED_RUN_WINDOW_SECONDS,
    hosted_global_run_limit: int = HOSTED_GLOBAL_RUN_LIMIT,
    hosted_max_concurrent: int = HOSTED_MAX_CONCURRENT_RUNS,
    hosted_max_tracked_clients: int = HOSTED_MAX_TRACKED_CLIENTS,
) -> FastAPI:
    """Create the loopback-only browser application."""
    app = FastAPI(
        title="Lifescape",
        docs_url=None,
        redoc_url=None,
        openapi_url=None if hosted_demo else "/openapi.json",
    )
    app.add_middleware(BodyLimitMiddleware)
    allowed_hosts = ["127.0.0.1", "localhost", "[::1]"]
    if hosted_demo:
        allowed_hosts.extend(["lifescape.buildproven.ai", "*.vercel.app"])
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts,
    )
    if hosted_demo:
        app.add_middleware(HostedStaticBoundaryMiddleware)
    package_dir = Path(__file__).resolve().parent
    template_environment = Environment(
        loader=FileSystemLoader(package_dir / "templates"),
        autoescape=select_autoescape(("html",)),
    )
    landing_template = template_environment.get_template("landing.html")
    demo_template = template_environment.get_template("demo.html")
    app_template = template_environment.get_template("app.html")
    app.mount("/static", StaticFiles(directory=package_dir / "static"), name="static")
    root_output = (output_dir or Path("outputs/app")).resolve()
    run_directories: dict[str, Path] = {}
    imported_evidence: OrderedDict[str, str] = OrderedDict()
    hosted_guard = HostedRunGuard(
        enabled=(not hosted_demo if hosted_runs_enabled is None else hosted_runs_enabled),
        run_limit=hosted_run_limit,
        window_seconds=hosted_run_window_seconds,
        global_run_limit=hosted_global_run_limit,
        max_concurrent=hosted_max_concurrent,
        max_tracked_clients=hosted_max_tracked_clients,
    )

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        if hosted_demo:
            return HTMLResponse(landing_template.render())
        return HTMLResponse(
            app_template.render(
                rail_note_title="Local by design",
                rail_note_body="Your evidence and outputs stay on this computer.",
            )
        )

    @app.get("/demo", include_in_schema=False)
    def finished_demo() -> Response:
        if not hosted_demo:
            return RedirectResponse("/")
        return HTMLResponse(demo_template.render())

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, object]:
        if hosted_demo:
            raise HTTPException(status_code=404, detail="the hosted site has no application API")
        with bundled_benchmark() as (evidence_path, config_dir):
            metric_ids = tuple(metric.id for metric in load_metrics(config_dir))
            fieldnames, rows = _read_evidence(evidence_path.read_text(encoding="utf-8"), metric_ids)
            profile = yaml.safe_load(
                (config_dir / "user_profile.example.yaml").read_text(encoding="utf-8")
            )
        return {
            "mode": "hosted-demo" if hosted_demo else "synthetic-demo",
            "allow_imports": not hosted_demo,
            "persistent_outputs": not hosted_demo,
            "places": _catalog(rows, metric_ids),
            "metric_count": len(metric_ids),
            "defaults": {
                "purchase_budget_max": profile["purchase_budget_max"],
                "future_self_age": profile["future_self_ages"][1],
                "household": "couple",
            },
            "field_count": len(fieldnames),
        }

    @app.post("/api/evidence/inspect")
    async def inspect_evidence(request: Request) -> dict[str, object]:
        if hosted_demo:
            raise HTTPException(status_code=404, detail="the hosted site has no application API")
        _validate_mutation_origin(request, trust_forwarded_proto=hosted_demo)
        try:
            raw_evidence = await request.body()
            if not raw_evidence:
                raise ValueError("evidence CSV is empty")
            csv_text = raw_evidence.decode("utf-8")
            with bundled_benchmark() as (_, config_dir):
                metric_ids = tuple(metric.id for metric in load_metrics(config_dir))
            _, rows = _read_evidence(csv_text, metric_ids)
            evidence_token = uuid4().hex[:12]
            imported_evidence[evidence_token] = csv_text
            while len(imported_evidence) > 8:
                imported_evidence.popitem(last=False)
            return {
                "places": _catalog(rows, metric_ids),
                "metric_count": len(metric_ids),
                "evidence_kind": _evidence_kind(rows),
                "evidence_token": evidence_token,
            }
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/run")
    def run_comparison(payload: AppRunRequest, request: Request) -> dict[str, object]:
        if hosted_demo:
            raise HTTPException(status_code=404, detail="the hosted site has no application API")
        _validate_mutation_origin(
            request,
            trust_forwarded_proto=hosted_demo,
            require_origin=hosted_demo,
        )
        guarded_run = False
        if hosted_demo:
            hosted_guard.acquire(_hosted_client_key(request))
            guarded_run = True
        token = uuid4().hex[:12]
        runs_root = root_output / "runs"
        run_dir = runs_root / token
        try:
            runs_root.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".staging-", dir=runs_root) as staging_name:
                staging_dir = Path(staging_name)
                with bundled_benchmark() as (benchmark_evidence, benchmark_config):
                    metric_ids = tuple(metric.id for metric in load_metrics(benchmark_config))
                    csv_text = benchmark_evidence.read_text(encoding="utf-8")
                    if payload.evidence_token is not None:
                        try:
                            csv_text = imported_evidence[payload.evidence_token]
                        except KeyError as exc:
                            raise ValueError(
                                "imported evidence is no longer available; import it again"
                            ) from exc
                    fieldnames, rows = _read_evidence(csv_text, metric_ids)
                    evidence_path = staging_dir / "evidence.csv"
                    _filter_evidence(
                        fieldnames, rows, set(payload.selected_place_ids), evidence_path
                    )
                    config_dir = benchmark_config
                    if payload.evidence_token is not None:
                        config_dir = staging_dir / "config"
                        shutil.copytree(benchmark_config, config_dir)
                        brief_path = config_dir / "research_brief.yaml"
                        brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
                        brief["benchmark_only"] = False
                        brief_path.write_text(
                            yaml.safe_dump(brief, sort_keys=False), encoding="utf-8"
                        )
                    profile_path = staging_dir / "profile.yaml"
                    _prepare_profile(
                        config_dir / "user_profile.example.yaml",
                        profile_path,
                        budget=payload.purchase_budget_max,
                        age=payload.future_self_age,
                        household=payload.household,
                    )
                    result = execute_run(
                        evidence_path=evidence_path,
                        config_dir=config_dir,
                        profile_path=profile_path,
                        database_path=staging_dir / "lifescape.sqlite",
                        output_dir=staging_dir,
                    )
                response = _response(result, token, downloads_enabled=not hosted_demo)
                if hosted_demo:
                    return response
                staging_dir.replace(run_dir)
            run_directories[token] = run_dir
            return response
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            if guarded_run:
                hosted_guard.release()

    @app.get("/api/downloads/{token}/{filename}")
    def download(
        token: Annotated[str, ApiPath(pattern=r"^[a-f0-9]{12}$")],
        filename: Annotated[str, ApiPath()],
    ) -> FileResponse:
        if hosted_demo:
            raise HTTPException(
                status_code=404,
                detail="downloads are available only in the local app",
            )
        if filename not in DOWNLOAD_FILES:
            raise HTTPException(status_code=404, detail="unknown report")
        run_dir = run_directories.get(token)
        if run_dir is None:
            raise HTTPException(status_code=404, detail="run is not available in this session")
        path = run_dir / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="report was not generated")
        return FileResponse(path, filename=filename)

    return app


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    output_dir: Path | None = None,
    open_browser: bool = True,
) -> None:
    """Launch the local app and optionally open its browser tab."""
    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(output_dir), host=host, port=port, log_level="warning")
