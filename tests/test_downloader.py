#!/usr/bin/env python
# -*- coding: utf8 -*-
"""
Offline tests for pynhanes.download (a local HTTP server stands in for NHANES)
"""

import io
import os
import time
import json
import zipfile
import threading
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pynhanes import downloader as dl


LISTING_HTML = """
<table id="GridView1">
<thead><tr><th>Years</th><th>Data File Name</th><th>Doc File</th><th>Data File</th><th>Date Published</th></tr></thead>
<tbody>
<tr><td>2017-2020</td><td>Demographic Variables</td><td><a href="/x/P_DEMO.htm">P_DEMO Doc</a></td>
    <td><a href="/Nchs/Data/Nhanes/Public/2017/DataFiles/P_DEMO.xpt">P_DEMO Data [XPT - 3.4 MB]</a></td><td>May 2021</td></tr>
<tr><td>2017-2020</td><td>Enterovirus</td><td><a href="#">P_SSEVD Doc</a></td>
    <td><a href="#">P_SSEVD Data [XPT - 83.8 KB]</a></td><td>Withdrawn</td></tr>
<tr><td>2017-2020</td><td>Raw 80hz</td><td><a href="/x/P_PAX80.htm">P_PAX80 Doc</a></td>
    <td><a href="https://ftp.cdc.gov/pub/pax_p">P_PAX80 Data [FTP]</a></td><td>October 2022</td></tr>
<tr><td>2017-2020</td><td>Physical Activity Monitor - Minute</td><td><a href="/x/P_PAXMIN.htm">P_PAXMIN Doc</a></td>
    <td><a href="https://ftp.cdc.gov/pub/NHANES/LargeDataFiles/P_PAXMIN.xpt">P_PAXMIN Data [XPT - 7.6 GB]</a></td><td>Updated October 2022</td></tr>
<tr><td>2017-2020</td><td>DXA</td><td><a href="/Nchs/Nhanes/Dxa/Dxa.aspx">All Years Doc</a></td>
    <td><a href="/Nchs/Nhanes/Dxa/Dxa.aspx">All Years Data</a></td><td>Updated December 2016</td></tr>
</tbody></table>
"""


def test_parse_listing_handles_years_column_and_unavailable_rows():
    files, unavailable = dl.parse_listing(LISTING_HTML, "Examination", dl.PREPANDEMIC)
    names = {f.name: f for f in files}
    assert set(names) == {"P_DEMO", "P_PAXMIN"}
    assert names["P_DEMO"].code == "DEMO"
    assert names["P_DEMO"].size == int(3.4 * 1024 ** 2)
    assert names["P_DEMO"].url == "https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/P_DEMO.xpt"
    assert names["P_PAXMIN"].activity is True
    assert names["P_DEMO"].doc_url == "https://wwwn.cdc.gov/x/P_DEMO.htm"
    reasons = sorted(u["reason"].split()[0] for u in unavailable)
    assert reasons == ["FTP", "external", "withdrawn"]


def test_survey_cache_ttl():
    final = [s for s in dl.SURVEYS if s.label == "2017-2018"][0]
    latest = [s for s in dl.SURVEYS if s.label == "2021-2023"][0]
    assert final.final and not latest.final
    assert dl.Survey("2025-2027", 2025, discovered=True).final is False


def test_discovered_survey_is_selectable_and_strips_suffix():
    new = dl.Survey("2025-2027", 2025, "", "", "Cycle=2025-2027", discovered=True)
    assert [s.label for s in dl.resolve_surveys("2025", extra=[new])] == ["2025-2027"]
    # not selected by default, only reported
    assert "2025-2027" not in [s.label for s in dl.resolve_surveys(None, extra=[new])]
    with pytest.raises(ValueError, match="Invalid survey"):
        dl.resolve_surveys("2025")
    assert dl._file_code("DEMO_M", new) == "DEMO"
    assert dl._file_code("RXQ_RX_M", new) == "RXQ_RX"
    assert dl._file_code("L13_2", new) == "L13_2"


def test_listing_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PYNHANES_CACHE", str(tmp_path / "cache"))
    assert "pynhanes" in dl.listing_cache_dir()
    path = dl.listing_cache_dir()
    assert path == str(tmp_path / "cache" / "pynhanes" / "listings")
    assert os.path.isdir(path)


def test_resolve_surveys():
    labels = lambda spec: [s.label for s in dl.resolve_surveys(spec)]
    assert labels("2021") == ["2021-2023"]
    assert labels("2017,2021") == ["2017-2018", "2021-2023"]
    assert labels([2007, "2017-2018"]) == ["2007-2008", "2017-2018"]
    assert len(labels(None)) == 11 and "2017-2020" not in labels(None)
    assert labels("prepandemic") == ["2017-2020"]
    for bad in ["2019", "2018", "1998", "recent", "J"]:
        with pytest.raises(ValueError, match="Invalid survey"):
            dl.resolve_surveys(bad)


def test_resolve_components():
    assert dl.resolve_components(None) == dl.COMPONENTS + ["Mortality"]
    assert dl.resolve_components("laboratory,Dietary") == ["Laboratory", "Dietary"]
    with pytest.raises(ValueError, match="Invalid component"):
        dl.resolve_components("Labs")


def test_resolve_activity():
    assert dl.resolve_activity(None) == []
    assert dl.resolve_activity("2011,2013") == [2011, 2013]
    assert dl.resolve_activity("all") == [2003, 2005, 2011, 2013]
    for bad in ["2017", "2009", "summary"]:
        with pytest.raises(ValueError, match="Invalid activity survey"):
            dl.resolve_activity(bad)


def test_resolve_output(tmp_path):
    folder = tmp_path / "XPT"
    assert dl.resolve_output(str(folder)) == str(folder)   # created
    assert folder.is_dir()
    afile = tmp_path / "afile.txt"
    afile.write_text("x")
    with pytest.raises(ValueError, match="not a folder"):
        dl.resolve_output(str(afile))
    with pytest.raises(ValueError, match="Can not create"):
        dl.resolve_output(str(afile / "sub"))


# ---------------------------------------------------------------------------
# Local server
# ---------------------------------------------------------------------------

class _Server:
    def __init__(self):
        self.files = {}      # path -> (bytes, last_modified_ts)
        self.requests = []
        self.encodings = set()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, head):
                server.requests.append((self.command, self.path, self.headers.get("Range")))
                server.encodings.add(self.headers.get("Accept-Encoding"))
                if self.path not in server.files:
                    self.send_response(404)
                    self.end_headers()
                    return
                data, ts = server.files[self.path]
                lm = formatdate(ts, usegmt=True)
                start, end = 0, len(data) - 1
                rng = self.headers.get("Range")
                spec = rng.split("=")[1] if rng else ""
                if spec.startswith("-"):                       # suffix range: last N bytes
                    start = max(0, len(data) - int(spec[1:]))
                elif spec:
                    first, _, last = spec.partition("-")
                    start = int(first)
                    end = min(int(last), end) if last else end
                if spec and start >= len(data):
                    self.send_response(416)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if spec and self.headers.get("If-Range") in (None, lm):
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                else:
                    self.send_response(200)
                    start, end = 0, len(data) - 1
                body = data[start:end + 1]
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Last-Modified", lm)
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                if not head:
                    self.wfile.write(body)

            def do_GET(self):
                self._send(False)

            def do_HEAD(self):
                self._send(True)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def add(self, path, data, ts=1700000000):
        self.files[path] = (data, ts)
        return self.url + path


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path_factory, monkeypatch):
    """
    Never touch the real ~/.cache/pynhanes during tests
    """
    monkeypatch.setenv("PYNHANES_CACHE", str(tmp_path_factory.mktemp("cache")))
    monkeypatch.setattr(dl, "MIN_REQUEST_INTERVAL", 0.0)   # no polite delay in tests


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.httpd.shutdown()


@pytest.fixture
def remote(monkeypatch, server):
    """
    Fake listing: DEMO_J (present locally), DEMO_L (missing), PAXRAW_C (zip, activity raw)
    """
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("PAXRAW_C.xpt", b"R" * 5000)
    files = [
        dl.RemoteFile("DEMO_J", "DEMO", "Demographics", "2017-2018",
                      server.add("/DEMO_J.xpt", b"J" * 1000), "xpt", 1000),
        dl.RemoteFile("DEMO_L", "DEMO", "Demographics", "2021-2023",
                      server.add("/DEMO_L.xpt", b"L" * 300000), "xpt", 300000),
        dl.RemoteFile("SMQ_L", "SMQ", "Questionnaire", "2021-2023",
                      server.add("/SMQ_L.xpt", b"S" * 2000), "xpt", 2000),
        dl.RemoteFile("PAXRAW_C", "PAXRAW", "Examination", "2003-2004",
                      server.add("/PAXRAW_C.zip", zbuf.getvalue()), "zip", len(zbuf.getvalue()),
                      activity=True),
        dl.RemoteFile("PAXMIN_G", "PAXMIN", "Examination", "2011-2012",
                      server.add("/PAXMIN_G.xpt", b"M" * 9000), "xpt", 9000, activity=True),
        dl.RemoteFile("PAXDAY_G", "PAXDAY", "Examination", "2011-2012",
                      server.add("/PAXDAY_G.xpt", b"D" * 700), "xpt", 700, activity=True),
        dl.RemoteFile("PAXHR_G", "PAXHR", "Examination", "2011-2012",
                      server.add("/PAXHR_G.xpt", b"H" * 4000), "xpt", 4000, activity=True),
    ]

    def fake_collect(surveys, components=None, cache_dir=None, log=print, refresh=False):
        labels = {s.label for s in surveys}
        comps = set(components or dl.COMPONENTS)
        return [f for f in files if f.survey in labels and f.component in comps], []

    monkeypatch.setattr(dl, "collect_remote_files", fake_collect)
    monkeypatch.setattr(dl, "discover_surveys", lambda log=print: [])
    return files


def _plan(folder, **kwargs):
    kwargs.setdefault("components", "Demographics,Dietary,Examination,Laboratory,Questionnaire")
    return dl.plan(str(folder), log=lambda *a: None, **kwargs)


def test_plan_skips_present_files_case_insensitive(tmp_path, remote):
    (tmp_path / "demo_j.XPT").write_bytes(b"J" * 1000)
    p = _plan(tmp_path)
    actions = {i.file.name: i.action for i in p.items}
    # accelerometry is never selected without -a
    assert actions == {"DEMO_J": "skip", "DEMO_L": "download", "SMQ_L": "download"}


def test_selection_by_survey_component_and_data_file(tmp_path, remote):
    assert {i.file.name for i in _plan(tmp_path, surveys="2021").actionable} == {"DEMO_L", "SMQ_L"}
    assert {i.file.name for i in _plan(tmp_path, components="Demographics").actionable} \
        == {"DEMO_J", "DEMO_L"}
    assert {i.file.name for i in _plan(tmp_path, data_files="SMQ").actionable} == {"SMQ_L"}
    # a code with no file in the selected survey is reported, not an error
    p = _plan(tmp_path, surveys="2017", data_files="DEMO,SMQ")
    assert {i.file.name for i in p.actionable} == {"DEMO_J"}
    assert p.selection["data_files_not_in_selection"] == ["SMQ"]
    assert "no file for SMQ" in p.summary()
    with pytest.raises(ValueError, match="Unknown data file code"):
        _plan(tmp_path, data_files="DEMOGRAPHICS")


def test_activity_selection(tmp_path, remote):
    # -a picks the minute-level file and its companion PAXDAY, not PAXHR
    p = _plan(tmp_path, activity=2011)
    assert {i.file.name for i in p.actionable} >= {"PAXMIN_G", "PAXDAY_G"}
    assert "PAXHR_G" not in {i.file.name for i in p.actionable}
    # -a works regardless of -s and -c
    p = _plan(tmp_path, surveys="2021", components="Demographics", activity=2011)
    assert {i.file.name for i in p.actionable} == {"DEMO_L", "PAXMIN_G", "PAXDAY_G"}
    # zip disk size is the uncompressed size
    # the size on disk of a .zip is read from the archive directory, not guessed
    p = _plan(tmp_path, surveys="2003", activity=2003)
    assert [i.file.name for i in p.actionable] == ["PAXRAW_C"]
    assert p.actionable[0].disk_size == 5000        # PAXRAW_C.xpt inside the archive
    # an explicitly named code still works
    assert [i.file.name for i in _plan(tmp_path, data_files="PAXHR").actionable] == ["PAXHR_G"]
    assert not _plan(tmp_path, surveys="2011").actionable   # 2011 has activity files only


def test_data_files_core_and_file(tmp_path, remote, monkeypatch):
    monkeypatch.setattr(dl, "PRESET_CORE", ["DEMO"])
    p = _plan(tmp_path, data_files="core")
    assert {i.file.name for i in p.actionable} == {"DEMO_J", "DEMO_L"}
    # a preset can be combined with explicit codes
    p = _plan(tmp_path, data_files="core,SMQ")
    assert {i.file.name for i in p.actionable} == {"DEMO_J", "DEMO_L", "SMQ_L"}
    # codes from a file (json list or plain text)
    codes = tmp_path / "codes.json"
    codes.write_text('["SMQ"]')
    assert {i.file.name for i in _plan(tmp_path, data_files=f"@{codes}").actionable} == {"SMQ_L"}
    codes.write_text("SMQ\nDEMO\n")
    assert len(_plan(tmp_path, data_files=f"@{codes}").actionable) == 3
    with pytest.raises(ValueError, match="Can not read data file codes"):
        _plan(tmp_path, data_files="@/nonexistent/codes.json")


def test_data_files_existing(tmp_path, remote):
    (tmp_path / "DEMO_J.xpt").write_bytes(b"J" * 1000)
    p = _plan(tmp_path, data_files="existing")
    assert {i.file.name for i in p.actionable} == {"DEMO_L"}


def test_overwrite_must_be_narrowed(tmp_path, remote):
    for kwargs in [{}, {"data_files": "full"}]:
        with pytest.raises(ValueError, match="every selected file of every survey"):
            _plan(tmp_path, overwrite=True, **kwargs)
    _plan(tmp_path, overwrite=True, surveys="2021")
    _plan(tmp_path, overwrite=True, data_files="DEMO")


def test_core_preset_is_the_default(tmp_path, remote, monkeypatch):
    monkeypatch.setattr(dl, "PRESET_CORE", ["DEMO"])
    p = _plan(tmp_path)
    assert p.selection["preset"] == "core"
    assert {i.file.name for i in p.actionable} == {"DEMO_J", "DEMO_L"}
    assert "core preset" in p.report() and "-d full" in p.report()
    p = _plan(tmp_path, data_files="full")
    assert p.selection["preset"] == "full"
    assert {i.file.name for i in p.actionable} == {"DEMO_J", "DEMO_L", "SMQ_L"}
    assert "all published by NHANES" in p.report()


def test_throttle_spaces_requests(monkeypatch):
    monkeypatch.setattr(dl, "MIN_REQUEST_INTERVAL", 0.05)
    dl._last_request.clear()
    start = time.monotonic()
    for _ in range(3):
        dl._throttle("https://wwwn.cdc.gov/x")
    assert time.monotonic() - start >= 0.1
    dl._throttle("https://ftp.cdc.gov/y")           # another server is not delayed
    assert time.monotonic() - start < 0.2


def test_run_downloads_extracts_and_is_idempotent(tmp_path, remote, server):
    (tmp_path / "DEMO_J.xpt").write_bytes(b"J" * 1000)
    p = _plan(tmp_path, activity="all")
    result = p.run(progress="none", log=lambda *a: None)
    assert not result["failed"]
    assert (tmp_path / "DEMO_L.xpt").read_bytes() == b"L" * 300000
    assert (tmp_path / "PAXRAW_C.xpt").read_bytes() == b"R" * 5000
    assert not (tmp_path / "PAXRAW_C.zip").exists()
    assert os.path.getmtime(tmp_path / "DEMO_L.xpt") == 1700000000
    manifest = json.loads((tmp_path / ".pynhanes" / "manifest.json").read_text())
    assert set(manifest) == {"DEMO_L", "SMQ_L", "PAXRAW_C", "PAXMIN_G", "PAXDAY_G"}
    assert not list((tmp_path / ".pynhanes" / "partial").iterdir())
    assert not _plan(tmp_path, activity="all").actionable


def test_resume_partial_download(tmp_path, remote, server):
    partial = tmp_path / ".pynhanes" / "partial"
    partial.mkdir(parents=True)
    (partial / "DEMO_L.xpt.part").write_bytes(b"L" * 100000)
    lm = formatdate(1700000000, usegmt=True)
    (partial / "DEMO_L.xpt.part.json").write_text(json.dumps({"url": remote[1].url, "last_modified": lm}))
    _plan(tmp_path, surveys="2021", data_files="DEMO").run(progress="none", log=lambda *a: None)
    assert (tmp_path / "DEMO_L.xpt").read_bytes() == b"L" * 300000
    assert ("GET", "/DEMO_L.xpt", "bytes=100000-") in server.requests
    assert server.encodings == {"identity"}


def test_stale_partial_larger_than_remote_restarts(tmp_path, remote, server):
    partial = tmp_path / ".pynhanes" / "partial"
    partial.mkdir(parents=True)
    (partial / "SMQ_L.xpt.part").write_bytes(b"x" * 5000)
    lm = formatdate(1700000000, usegmt=True)
    (partial / "SMQ_L.xpt.part.json").write_text(json.dumps({"url": remote[2].url, "last_modified": lm}))
    result = _plan(tmp_path, surveys="2021", data_files="SMQ").run(progress="none", log=lambda *a: None)
    assert not result["failed"]
    assert (tmp_path / "SMQ_L.xpt").read_bytes() == b"S" * 2000


def test_website_updates_are_reported_and_overwrite_refetches(tmp_path, remote, server):
    _plan(tmp_path, surveys="2021").run(progress="none", log=lambda *a: None)
    assert not _plan(tmp_path, surveys="2021", check_updates=True).updated
    # website publishes a new version of DEMO_L
    server.add("/DEMO_L.xpt", b"N" * 310000, ts=1800000000)
    p = _plan(tmp_path, surveys="2021", check_updates=True)
    assert [u["name"] for u in p.updated] == ["DEMO_L"]
    assert not p.actionable          # reported, but not downloaded without -y
    assert "--yes -s 2021" in p.summary()
    # -y re-downloads the selected files
    p = _plan(tmp_path, surveys="2021", data_files="DEMO", overwrite=True)
    assert [(i.file.name, i.action) for i in p.actionable] == [("DEMO_L", "redownload")]
    p.run(progress="none", log=lambda *a: None)
    assert (tmp_path / "DEMO_L.xpt").read_bytes() == b"N" * 310000
    assert (tmp_path / "SMQ_L.xpt").read_bytes() == b"S" * 2000   # untouched


def test_not_enough_disk_space(tmp_path, remote, monkeypatch):
    import collections
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(dl.shutil, "disk_usage", lambda p: usage(1, 1, 1024))
    p = _plan(tmp_path, surveys="2021")
    assert not p.fits
    with pytest.raises(dl.InsufficientSpaceError):
        p.run(progress="none", log=lambda *a: None)
    assert dl.main(["-o", str(tmp_path), "-s", "2021"]) == dl.EXIT_NO_SPACE


def test_cli(tmp_path, remote, capsys):
    # dry run downloads nothing
    assert dl.main(["-o", str(tmp_path), "-n"]) == dl.EXIT_OK
    out = capsys.readouterr().out
    assert "ALREADY DOWNLOADED" in out and "TO DOWNLOAD NOW" in out
    assert "MINUTE-LEVEL ACTIVITY DATA" in out
    assert "--> occupied:" in out and "--> download" in out
    assert not (tmp_path / "DEMO_L.xpt").exists()
    # invalid values are refused
    for argv, msg in [(["-s", "2019"], "Invalid survey"), (["-c", "Labs"], "Invalid component"),
                      (["-a", "2017"], "Invalid activity survey"),
                      (["--yes"], "every selected file of every survey"),
                      (["-d", "NOSUCH"], "Unknown data file code")]:
        capsys.readouterr()
        assert dl.main(["-o", str(tmp_path)] + argv) == dl.EXIT_ERROR
        assert msg in capsys.readouterr().out
    # download, then re-run
    assert dl.main(["-o", str(tmp_path), "-s", "2021", "-c", "Demographics"]) == dl.EXIT_OK
    assert (tmp_path / "DEMO_L.xpt").exists()
    capsys.readouterr()
    assert dl.main(["-o", str(tmp_path), "-s", "2021", "-c", "Demographics"]) == dl.EXIT_OK
    assert "already present" in capsys.readouterr().out
    # --yes prints the advice about re-downloading older surveys
    capsys.readouterr()
    assert dl.main(["-o", str(tmp_path), "-s", "2021", "--yes", "-n"]) == dl.EXIT_OK
    assert "NOTE on --yes" in capsys.readouterr().out


def test_cli_json(tmp_path, remote, capsys):
    assert dl.main(["-o", str(tmp_path), "-s", "2021", "-n", "-j"]) == dl.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["counts"]["download"] == 2
    assert data["selection"]["surveys"] == ["2021-2023"]
    assert data["selection"]["components"] == dl.COMPONENTS + ["Mortality"]
    assert {f["name"] for f in data["files"]} == {"DEMO_L", "SMQ_L"}
