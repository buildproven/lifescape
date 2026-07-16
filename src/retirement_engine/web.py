"""Local browser workspace for guided retirement comparisons."""

from __future__ import annotations

import csv
import io
import shutil
import threading
import webbrowser
from pathlib import Path
from typing import Annotated, Literal, TypedDict
from uuid import uuid4

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from retirement_engine.config import load_metrics
from retirement_engine.models import GateState, RunResult
from retirement_engine.pipeline import execute_run
from retirement_engine.resources import bundled_benchmark

MAX_EVIDENCE_BYTES = 5_000_000
DOWNLOAD_FILES = frozenset({"comparison.md", "comparison.csv", "sensitivity.csv"})


class WebModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceInspectRequest(WebModel):
    csv_text: str = Field(min_length=1)


class AppRunRequest(WebModel):
    selected_place_ids: tuple[str, ...] = Field(min_length=2)
    purchase_budget_max: float = Field(ge=50_000, le=100_000_000)
    future_self_age: int = Field(ge=40, le=110)
    household: Literal["solo", "couple", "family"] = "couple"
    evidence_csv: str | None = None

    @field_validator("selected_place_ids")
    @classmethod
    def places_are_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("selected places must be unique")
        return values

    @field_validator("evidence_csv")
    @classmethod
    def evidence_is_bounded(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > MAX_EVIDENCE_BYTES:
            raise ValueError("evidence CSV exceeds the 5 MB local-app limit")
        return value


class CatalogPlace(TypedDict):
    place_id: str
    name: str
    state: str
    complete_metrics: int
    total_metrics: int


def _read_evidence(
    csv_text: str, metric_ids: tuple[str, ...]
) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise ValueError("evidence CSV has no header")
    required = {"place_id", "place_name", "state", *metric_ids}
    missing = sorted(required - set(reader.fieldnames))
    if missing:
        raise ValueError(f"evidence CSV is missing columns: {missing}")
    rows = [dict(row) for row in reader]
    if not rows:
        raise ValueError("evidence CSV has no rows")
    return list(reader.fieldnames), rows


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


def _response(run: RunResult, token: str) -> dict[str, object]:
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
    return {
        "run_id": run.run_id,
        "evaluated_as_of": run.evaluated_as_of.isoformat(),
        "evidence_through": run.evidence_through.date().isoformat(),
        "synthetic": all(observation.source.synthetic for observation in run.observations),
        "rankings": rankings,
        "blocked": blocked,
        "downloads": {name: f"/api/downloads/{token}/{name}" for name in sorted(DOWNLOAD_FILES)},
    }


def create_app(output_dir: Path | None = None) -> FastAPI:
    """Create the loopback-only browser application."""
    app = FastAPI(title="Lifescape", docs_url=None, redoc_url=None)
    package_dir = Path(__file__).resolve().parent
    app.mount("/static", StaticFiles(directory=package_dir / "static"), name="static")
    root_output = (output_dir or Path("outputs/app")).resolve()
    run_directories: dict[str, Path] = {}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(package_dir / "templates/app.html")

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, object]:
        with bundled_benchmark() as (evidence_path, config_dir):
            metric_ids = tuple(metric.id for metric in load_metrics(config_dir))
            fieldnames, rows = _read_evidence(evidence_path.read_text(encoding="utf-8"), metric_ids)
            profile = yaml.safe_load(
                (config_dir / "user_profile.example.yaml").read_text(encoding="utf-8")
            )
        return {
            "mode": "synthetic-demo",
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
    def inspect_evidence(request: EvidenceInspectRequest) -> dict[str, object]:
        try:
            with bundled_benchmark() as (_, config_dir):
                metric_ids = tuple(metric.id for metric in load_metrics(config_dir))
            _, rows = _read_evidence(request.csv_text, metric_ids)
            return {
                "places": _catalog(rows, metric_ids),
                "metric_count": len(metric_ids),
                "evidence_kind": _evidence_kind(rows),
            }
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/run")
    def run_comparison(request: AppRunRequest) -> dict[str, object]:
        token = uuid4().hex[:12]
        run_dir = root_output / "runs" / token
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            with bundled_benchmark() as (benchmark_evidence, benchmark_config):
                metric_ids = tuple(metric.id for metric in load_metrics(benchmark_config))
                csv_text = request.evidence_csv or benchmark_evidence.read_text(encoding="utf-8")
                fieldnames, rows = _read_evidence(csv_text, metric_ids)
                evidence_path = run_dir / "evidence.csv"
                _filter_evidence(fieldnames, rows, set(request.selected_place_ids), evidence_path)
                config_dir = benchmark_config
                if request.evidence_csv is not None:
                    config_dir = run_dir / "config"
                    shutil.copytree(benchmark_config, config_dir)
                    brief_path = config_dir / "research_brief.yaml"
                    brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
                    brief["benchmark_only"] = False
                    brief_path.write_text(yaml.safe_dump(brief, sort_keys=False), encoding="utf-8")
                profile_path = run_dir / "profile.yaml"
                _prepare_profile(
                    config_dir / "user_profile.example.yaml",
                    profile_path,
                    budget=request.purchase_budget_max,
                    age=request.future_self_age,
                    household=request.household,
                )
                result = execute_run(
                    evidence_path=evidence_path,
                    config_dir=config_dir,
                    profile_path=profile_path,
                    database_path=root_output / "lifescape.sqlite",
                    output_dir=run_dir,
                )
            run_directories[token] = run_dir
            return _response(result, token)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/downloads/{token}/{filename}")
    def download(
        token: Annotated[str, ApiPath(pattern=r"^[a-f0-9]{12}$")],
        filename: Annotated[str, ApiPath()],
    ) -> FileResponse:
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
