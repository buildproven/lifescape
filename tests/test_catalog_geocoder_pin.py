"""The catalog rebuild reads a committed Census Geocoder response (BUI-1722, PRD FR4, FR5).

REQ-CAT-1: the builder reads geocoder results from the committed file and POSTs to Census only
with ``--refresh-geocoder``, which rewrites that file.
REQ-CAT-2: ``--verify`` makes no geocoder call and the rebuild matches the committed catalog and
manifest byte for byte.
REQ-CAT-3: ``--verify`` fails, naming both digests, when the committed response's SHA-256 differs
from the one the manifest records.
REQ-CAT-4: the shipped catalog and manifest were rebuilt from the committed response.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_place_catalog.py"
spec = importlib.util.spec_from_file_location("build_place_catalog_geocoder_pin", SCRIPT)
assert spec and spec.loader
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

COMMITTED_RESPONSE = ROOT / "data/place-catalog/census-geocoder-response.csv"
MATCHED = (
    b'"340001","1 MAIN ST, TESTVILLE, NC, 28001","Match","Exact",'
    b'"1 MAIN ST, TESTVILLE, NC, 28001","-80.05,35.02","1","L"\n'
)
UNMATCHED = b'"340002","2 ELM ST, TESTVILLE, NC, 28001","No_Match"\n'
RESPONSE = MATCHED + UNMATCHED


def _pinned(response: bytes) -> bytes:
    """A response as the builder pins it: rows sorted by facility id, minimal CSV quoting."""
    pinned: bytes = build.csv_bytes(sorted(csv.reader(io.StringIO(response.decode("utf-8")))))
    return pinned


def _zip(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def _tsv(*rows: tuple[str, ...]) -> str:
    return "".join("\t".join(row) + "\n" for row in rows)


def _acs(variables: dict[str, str], *extra: str) -> bytes:
    header = "|".join(("GEO_ID", *variables))
    row = "|".join(("1600000US3700001", *variables.values()))
    return "\n".join((header, row, *extra)).encode() + b"\n"


def _noaa() -> bytes:
    text = (
        b"STATION,NAME,LATITUDE,LONGITUDE,ANN-TMIN-AVGNDS-LSTH032,ANN-TMAX-AVGNDS-GRTH090,"
        b"ANN-PRCP-NORMAL,ANN-SNOW-NORMAL\n"
        b'USW00000001,"TESTVILLE AIRPORT, NC US",35.01,-80.01,60.0,40.0,45.0,5.0\n'
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo("USW00000001.csv")
        member.size = len(text)
        archive.addfile(member, io.BytesIO(text))
    return buffer.getvalue()


def _lakes() -> bytes:
    ring = [[-87.0, 45.0], [-86.0, 45.0], [-86.0, 46.0], [-87.0, 45.0]]
    features = [
        {"properties": {"name": lake}, "geometry": {"type": "Polygon", "coordinates": [ring]}}
        for lake in build.GREAT_LAKES
    ]
    return json.dumps({"features": features}).encode()


def _inputs() -> dict[str, bytes]:
    """Small official-format input files for one serving place, keyed by their cache name."""
    shares = {f"B01001_E{n:03d}": "500" for n in (*range(20, 26), *range(44, 50))}
    return {
        "2024_Gaz_place_national.zip": _zip(
            "2024_Gaz_place_national.txt",
            _tsv(
                ("USPS", "GEOID", "NAME", "ALAND_SQMI", "INTPTLAT", "INTPTLONG"),
                ("NC", "3700001", "Testville city", "10.0", "35.0", "-80.0"),
            ),
        ),
        "acsdt5y2024-b01003.dat": _acs({"B01003_E001": "50000"}, "310M700US99999|2000000"),
        "acsdt5y2024-b25077.dat": _acs({"B25077_E001": "300000"}),
        "acsdt5y2024-b08301.dat": _acs(
            {"B08301_E001": "20000", "B08301_E010": "500", "B08301_E018": "100"}
            | {"B08301_E019": "400", "B08301_E021": "2000"}
        ),
        "acsdt5y2024-b15003.dat": _acs(
            {"B15003_E001": "30000"} | {f"B15003_E{n:03d}": "2500" for n in range(22, 26)}
        ),
        "acsdt5y2024-b01001.dat": _acs({"B01001_E001": "50000"} | shares),
        "noaa-normals-1991-2020-annualseasonal.tar.gz": _noaa(),
        "2024_Gaz_cbsa_national.zip": _zip(
            "2024_Gaz_cbsa_national.txt",
            _tsv(("GEOID", "NAME", "CBSA_TYPE"), ("99999", "Testville, NC Metro Area", "1")),
        ),
        "ne_10m_coastline.geojson": json.dumps(
            {
                "features": [
                    {
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[-75.0, 35.0], [-75.0, 36.0]],
                        }
                    }
                ]
            }
        ).encode(),
        "ne_10m_lakes.geojson": _lakes(),
        "gnis-AllStates-20210825.zip": _zip(
            "NC_Features_20210825.txt",
            "FEATURE_NAME|FEATURE_CLASS|STATE_ALPHA|PRIM_LAT_DEC|PRIM_LONG_DEC|ELEV_IN_FT\n"
            "Testville|Populated Place|NC|35.0|-80.0|700\n",
        ),
        "2024_Gaz_zcta_national.zip": _zip(
            "2024_Gaz_zcta_national.txt",
            _tsv(("GEOID", "INTPTLAT", "INTPTLONG"), ("28001", "35.1", "-80.1")),
        ),
        "cms-Hospital_General_Information.csv": (
            b'"Facility ID","Facility Name","Address","City/Town","State","ZIP Code",'
            b'"Emergency Services"\n'
            b'"340001","TESTVILLE GENERAL","1 MAIN ST","TESTVILLE","NC","28001","Yes"\n'
            b'"340002","TESTVILLE EAST","2 ELM ST","TESTVILLE","NC","28001","Yes"\n'
        ),
    }


class Repository:
    """The builder pointed at a temporary checkout, with every network call recorded."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.catalog = root / "src/lifescape/data/place-catalog.csv.gz"
        self.manifest = root / "src/lifescape/data/place-catalog.manifest.json"
        self.response = root / "data/place-catalog/census-geocoder-response.csv"
        self.cache = root / ".cache/place-catalog"
        self.cache.mkdir(parents=True)
        for name, data in _inputs().items():
            (self.cache / name).write_bytes(data)
        self.posts: list[bytes] = []
        self.answer: bytes | None = RESPONSE
        self.downloads: list[object] = []
        monkeypatch.setattr(build, "CATALOG_PATH", self.catalog)
        monkeypatch.setattr(build, "MANIFEST_PATH", self.manifest)
        monkeypatch.setattr(build, "GEOCODER_RESPONSE_PATH", self.response)
        monkeypatch.setattr(build, "CACHE", self.cache)
        monkeypatch.setattr(build, "post_geocoder_batch", self._post)
        monkeypatch.setattr(urllib.request, "urlopen", self._urlopen)

    def _post(self, addresses: bytes) -> bytes:
        self.posts.append(addresses)
        if self.answer is None:
            raise AssertionError("the Census Geocoder was called")
        return self.answer

    def _urlopen(self, request: object, *args: Any, **kwargs: Any) -> object:
        self.downloads.append(request)
        raise AssertionError(f"network call to {request!r}")

    def offline(self) -> None:
        """Fail any geocoder POST and drop the scratch cache copy of the response."""
        self.answer = None
        for cached in self.cache.glob("census-geocoder-*.csv"):
            cached.unlink()

    def recorded_digest(self) -> str:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        digest: str = manifest["inputs"]["census_geocoder"]["sha256"]
        return digest


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repository:
    return Repository(tmp_path, monkeypatch)


@pytest.fixture
def pinned(repo: Repository) -> Repository:
    """A checkout whose catalog, manifest, and pinned response came from one refresh."""
    assert build.main(["--refresh-geocoder"]) == 0
    repo.offline()
    return repo


def test_req_cat_1_refresh_posts_once_and_writes_the_pinned_response(repo: Repository) -> None:
    assert build.main(["--refresh-geocoder"]) == 0

    assert len(repo.posts) == 1
    assert repo.response.read_bytes() == _pinned(RESPONSE)
    assert repo.recorded_digest() == hashlib.sha256(_pinned(RESPONSE)).hexdigest()
    assert repo.downloads == []


def test_req_cat_1_refresh_rewrites_an_existing_pinned_response(pinned: Repository) -> None:
    pinned.answer = MATCHED + b'"340002","2 ELM ST, TESTVILLE, NC, 28001","Tie"\n'

    assert build.main(["--refresh-geocoder"]) == 0

    assert len(pinned.posts) == 2
    assert pinned.response.read_bytes() == _pinned(pinned.answer)
    assert pinned.recorded_digest() == hashlib.sha256(_pinned(pinned.answer)).hexdigest()


def test_req_cat_1_build_reads_the_committed_response_and_never_posts(pinned: Repository) -> None:
    catalog, manifest = pinned.catalog.read_bytes(), pinned.manifest.read_bytes()
    pinned.catalog.unlink()
    pinned.manifest.unlink()

    assert build.main([]) == 0

    assert len(pinned.posts) == 1  # only the refresh that pinned it
    assert pinned.catalog.read_bytes() == catalog
    assert pinned.manifest.read_bytes() == manifest
    assert pinned.response.read_bytes() == _pinned(RESPONSE)


def test_req_cat_1_without_a_committed_response_the_build_stops_and_never_posts(
    repo: Repository,
) -> None:
    repo.answer = None

    with pytest.raises(SystemExit, match="--refresh-geocoder"):
        build.main([])

    assert repo.posts == []
    assert not repo.catalog.exists()


def test_req_cat_1_a_response_for_another_cms_file_stops_the_build(pinned: Repository) -> None:
    pinned.response.write_bytes(MATCHED)  # facility 340002 is missing

    with pytest.raises(SystemExit, match="--refresh-geocoder") as raised:
        build.main([])

    assert "340002" in str(raised.value.code)
    assert len(pinned.posts) == 1


def test_req_cat_1_verify_and_refresh_cannot_run_together(pinned: Repository) -> None:
    with pytest.raises(SystemExit) as raised:
        build.main(["--verify", "--refresh-geocoder"])

    assert raised.value.code == 2
    assert len(pinned.posts) == 1


def test_req_cat_2_verify_rebuilds_byte_for_byte_with_the_network_failing(
    pinned: Repository, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog, manifest = pinned.catalog.read_bytes(), pinned.manifest.read_bytes()

    assert build.main(["--verify"]) == 0

    assert len(pinned.posts) == 1  # only the refresh that pinned it
    assert pinned.downloads == []
    assert pinned.catalog.read_bytes() == catalog
    assert pinned.manifest.read_bytes() == manifest
    assert "matches" in capsys.readouterr().out


def test_req_cat_2_verify_fails_when_the_manifest_text_differs(
    pinned: Repository, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = json.loads(pinned.manifest.read_text(encoding="utf-8"))
    manifest["geography"] = "edited by hand"
    pinned.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", "utf-8")

    assert build.main(["--verify"]) == 1

    assert pinned.manifest.name in capsys.readouterr().err


def test_req_cat_2_verify_fails_when_the_compressed_catalog_bytes_differ(
    pinned: Repository, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = gzip.decompress(pinned.catalog.read_bytes())
    pinned.catalog.write_bytes(gzip.compress(raw, compresslevel=9, mtime=1))  # same rows

    assert build.main(["--verify"]) == 1

    assert pinned.catalog.name in capsys.readouterr().err


def test_req_cat_3_a_tampered_response_fails_verify_naming_both_digests(
    pinned: Repository,
) -> None:
    tampered = pinned.response.read_bytes().replace(b"-80.05,35.02", b"-80.06,35.02")
    assert tampered != pinned.response.read_bytes()
    pinned.response.write_bytes(tampered)

    with pytest.raises(SystemExit) as raised:
        build.main(["--verify"])

    message = str(raised.value.code)
    assert hashlib.sha256(tampered).hexdigest() in message
    assert pinned.recorded_digest() in message
    assert len(pinned.posts) == 1
    assert pinned.downloads == []


def test_req_cat_3_a_missing_response_fails_verify_before_any_download(
    pinned: Repository,
) -> None:
    pinned.response.unlink()
    for name in _inputs():
        (pinned.cache / name).unlink()  # a rebuild would have to download

    with pytest.raises(SystemExit, match="--refresh-geocoder"):
        build.main(["--verify"])

    assert pinned.downloads == []
    assert len(pinned.posts) == 1


def test_req_cat_4_the_shipped_manifest_pins_the_committed_response() -> None:
    manifest = json.loads(build.MANIFEST_PATH.read_text(encoding="utf-8"))
    data = COMMITTED_RESPONSE.read_bytes()

    assert build.GEOCODER_RESPONSE_PATH == COMMITTED_RESPONSE
    assert manifest["inputs"]["census_geocoder"]["sha256"] == hashlib.sha256(data).hexdigest()
    rows = [row for row in csv.reader(io.StringIO(data.decode("utf-8"))) if row]
    assert [row[0] for row in rows] == sorted(row[0] for row in rows)
    counts = manifest["hospital_facilities"]
    assert len(rows) == counts["emergency_facilities"]
    assert sum(row[2] == "Match" for row in rows) == counts["census_geocoder"]


def test_req_cat_4_changelog_says_hospital_distances_may_shift() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## Unreleased", 1)[1].split("\n## ", 1)[0]
    entry = next(item for item in unreleased.split("\n- ") if "BUI-1722" in item)

    assert "hospital" in entry.casefold()
    assert "re-matched" in entry


def test_req_cat_1_a_rejected_refresh_leaves_the_pinned_response_and_catalog(
    pinned: Repository,
) -> None:
    response, catalog = pinned.response.read_bytes(), pinned.catalog.read_bytes()
    pinned.answer = b"<html>Service Unavailable</html>\n"

    with pytest.raises(SystemExit, match="unrequested facility"):
        build.main(["--refresh-geocoder"])

    assert len(pinned.posts) == 2
    assert pinned.response.read_bytes() == response
    assert pinned.catalog.read_bytes() == catalog


def test_req_cat_2_verify_fails_when_the_committed_catalog_is_missing(
    pinned: Repository, capsys: pytest.CaptureFixture[str]
) -> None:
    pinned.catalog.unlink()

    assert build.main(["--verify"]) == 1

    assert pinned.catalog.name in capsys.readouterr().err
    assert not pinned.catalog.exists()
