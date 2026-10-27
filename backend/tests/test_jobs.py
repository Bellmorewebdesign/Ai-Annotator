"""API, worker, recovery and end-to-end pipeline tests (scripted AI, no network)."""

from __future__ import annotations

import io
import json
import time

import pikepdf
import pytest
from fastapi.testclient import TestClient
from fixtures import figure_table_pdf, simple_pdf, two_column_pdf

from annotator.ai.client import AITransientError
from annotator.ai.fake import FakeClaude
from annotator.api.app import create_app
from annotator.config import Settings
from annotator.jobs.store import JobStore
from annotator.storage import JobFiles
from annotator.worker.main import Worker


def make_settings(tmp, **kw) -> Settings:
    env = {"ANNOTATOR_ENV": "test", "ANNOTATOR_AI_PROVIDER": "fake", "ANNOTATOR_DATA_DIR": str(tmp / "data"),
           "ANNOTATOR_LEASE_SECONDS": "30", "ANTHROPIC_API_KEY": ""}
    env.update({k: str(v) for k, v in kw.items()})
    return Settings(_env_file=None, **env)


@pytest.fixture
def env(tmp):
    s = make_settings(tmp)
    client = TestClient(create_app(s))
    return s, client


def upload(client, path, instructions="Highlight the main claims and evidence.", options=None, extra=None):
    files = {"document": (path.name, path.read_bytes(), "application/octet-stream")}
    if extra:
        files.update(extra)
    data = {"instructions": instructions, "options": json.dumps(options or {"density": "balanced"})}
    return client.post("/api/jobs", files=files, data=data)


def run_worker(s, fake=None) -> Worker:
    fake = fake or FakeClaude()
    w = Worker(s, client_factory=lambda: fake)
    w.fake = fake
    assert w.run_once()
    return w


# ----------------------------------------------------------------------------------------


def test_health_reports_capabilities(env):
    s, client = env
    h = client.get("/api/health").json()
    assert h["ai"]["configured"] and h["ai"]["test_mode"]
    assert h["limits"]["max_pages"] == 50 and h["worker"]["alive"] is False
    assert len(h["default_categories"]) == 6


def test_end_to_end_pdf_job(env, tmp):
    s, client = env
    src = tmp / "paper.pdf"
    two_column_pdf(src)
    r = upload(client, src)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued" and job["document"]["pages"] == 2 and len(job["id"]) == 22
    assert job["queue_position"] == 0

    w = run_worker(s)
    view = client.get(f"/api/jobs/{job['id']}").json()
    assert view["status"] == "completed", view["error"]
    res = view["result"]
    assert res["annotations"] and res["stats"]["placed"] == len(res["annotations"])
    assert all(a["match"] in ("exact", "relocated", "approximate", "region", "caption") for a in res["annotations"])
    assert sum(c["count"] for c in res["categories"]) == len(res["annotations"])
    phases = {p["key"]: p["status"] for p in view["progress"]["phases"]}
    assert all(v in ("done", "skipped") for v in phases.values())
    assert view["metrics"]["ai_requests"] >= 2  # categories + analysis + summary
    assert "summary" in w.fake.calls

    pdf = client.get(view["files"]["annotated"])
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert "paper%20%28annotated%29.pdf" in pdf.headers["content-disposition"]
    with pikepdf.open(io.BytesIO(pdf.content)) as doc:
        n = sum(len(p.obj.get("/Annots", [])) for p in doc.pages)
    assert n >= len(res["annotations"])
    md = client.get(view["files"]["summary_md"]).text
    assert "# " in md and "Annotated passages" in md
    assert client.get(view["files"]["summary_pdf"]).content.startswith(b"%PDF")
    # Work files (extracted text, AI responses) are removed after completion.
    assert not JobFiles.for_job(s.jobs_dir, job["id"]).work_dir.exists()


def test_custom_categories_and_disabled_features(env, tmp):
    s, client = env
    src = tmp / "fig.pdf"
    figure_table_pdf(src)
    opts = {"density": "detailed", "categories_mode": "custom", "allow_shapes": False, "allow_underline": False,
            "categories": [{"id": "results", "name": "Results", "color": "#a5d8ff", "description": "Findings"}]}
    job = upload(client, src, options=opts).json()
    w = run_worker(s)
    view = client.get(f"/api/jobs/{job['id']}").json()
    assert view["status"] == "completed"
    assert "categories" not in w.fake.calls  # custom categories skip detection
    anns = view["result"]["annotations"]
    assert anns and {a["category"] for a in anns} == {"Results"} and {a["kind"] for a in anns} == {"highlight"}
    assert view["result"]["categories"][0]["color"] == "#A5D8FF"


def test_instruction_file_and_text_document(env, tmp):
    s, client = env
    doc = tmp / "notes.txt"
    doc.write_text("Photosynthesis converts light energy into chemical energy stored in glucose molecules. "
                   "Chlorophyll absorbs mostly red and blue light while reflecting green light.\n\n"
                   "The Calvin cycle uses ATP and NADPH to fix carbon dioxide into sugars over several steps.",
                   encoding="utf-8")
    rubric = tmp / "rubric.txt"
    rubric.write_text("Highlight definitions in purple and processes in green.", encoding="utf-8")
    r = upload(client, doc, instructions="", extra={"instructions_file": ("rubric.txt", rubric.read_bytes(), "text/plain")})
    assert r.status_code == 201, r.text
    assert r.json()["instructions_file"]["name"] == "rubric.txt"
    run_worker(s)
    view = client.get(f"/api/jobs/{r.json()['id']}").json()
    assert view["status"] == "completed", view["error"]
    names = [c["name"] for c in view["result"]["categories"]]
    assert names == ["Definitions", "Processes"]  # detected from the attached rubric


@pytest.mark.parametrize("content,status,code", [
    (b"GIF89a" + b"\x00" * 200, 415, "unsupported_file"),
    (b"%PDF-1.4\nnot really a pdf", 422, "pdf_unreadable"),
    (b"", 400, "empty_file"),
])
def test_upload_validation_errors(env, tmp, content, status, code):
    s, client = env
    f = tmp / "bad.pdf"
    f.write_bytes(content)
    r = upload(client, f)
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    assert "Traceback" not in r.text
    assert not any((s.jobs_dir).iterdir()), "rejected uploads must not leave files behind"


def test_upload_limits(tmp):
    s = make_settings(tmp, ANNOTATOR_MAX_UPLOAD_MB=1, ANNOTATOR_MAX_PAGES=1)
    client = TestClient(create_app(s))
    src = tmp / "two.pdf"
    two_column_pdf(src, pages=2)
    r = upload(client, src)
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many_pages"
    big = tmp / "big.txt"
    big.write_bytes(b"word " * 300_000)
    r = upload(client, big)
    assert r.status_code == 413
    r = upload(client, src, options={"density": "extreme"})
    assert r.status_code == 422


def test_invalid_and_unknown_job_ids(env):
    s, client = env
    assert client.get("/api/jobs/../../etc/passwd").status_code == 404
    assert client.get("/api/jobs/short").status_code == 404
    assert client.get("/api/jobs/" + "a" * 22).status_code == 404
    assert client.get("/api/jobs/" + "a" * 22 + "/files/annotated").status_code == 404


def test_delete_job_removes_files(env, tmp):
    s, client = env
    src = tmp / "s.pdf"
    simple_pdf(src)
    job = upload(client, src).json()
    run_worker(s)
    root = JobFiles.for_job(s.jobs_dir, job["id"]).root
    assert root.exists()
    assert client.delete(f"/api/jobs/{job['id']}").json() == {"deleted": True}
    assert not root.exists()
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_ai_failure_marks_job_failed_with_safe_message(env, tmp):
    s, client = env
    src = tmp / "s.pdf"
    simple_pdf(src)
    job = upload(client, src).json()
    fake = FakeClaude(fail_times=10, error=AITransientError("The AI service is busy right now.", "ai_rate_limited"))
    run_worker(s, fake)
    view = client.get(f"/api/jobs/{job['id']}").json()
    assert view["status"] == "failed"
    assert view["error"] == {"code": "ai_rate_limited", "message": "The AI service is busy right now."}


def test_ai_not_configured_rejects_jobs(tmp):
    s = make_settings(tmp, ANNOTATOR_AI_PROVIDER="anthropic", ANTHROPIC_API_KEY="")
    client = TestClient(create_app(s))
    src = tmp / "s.pdf"
    simple_pdf(src)
    r = upload(client, src)
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["error"]["message"]


def test_interrupted_job_is_recovered_by_another_worker(env, tmp):
    s, client = env
    src = tmp / "s.pdf"
    simple_pdf(src)
    job = upload(client, src).json()
    store = JobStore(s.db_path)
    # A worker claims the job and then dies without finishing (its lease expires).
    claimed = store.claim_next("dead-worker", lease_seconds=1, max_attempts=3)
    assert claimed.id == job["id"]
    assert store.claim_next("other", lease_seconds=30, max_attempts=3) is None  # lease still valid
    time.sleep(1.1)
    run_worker(s)
    view = client.get(f"/api/jobs/{job['id']}").json()
    assert view["status"] == "completed" and view["attempts"] == 2


def test_job_fails_after_too_many_interruptions(env, tmp):
    s, client = env
    src = tmp / "s.pdf"
    simple_pdf(src)
    job = upload(client, src).json()
    store = JobStore(s.db_path)
    for _ in range(3):
        assert store.claim_next("dying", lease_seconds=0, max_attempts=3) is not None
        time.sleep(0.01)
    assert store.claim_next("next", lease_seconds=30, max_attempts=3) is None
    view = client.get(f"/api/jobs/{job['id']}").json()
    assert view["status"] == "failed" and view["error"]["code"] == "interrupted"


def test_resumed_job_reuses_saved_ai_analysis(env, tmp):
    s, client = env
    src = tmp / "s.pdf"
    two_column_pdf(src)
    job = upload(client, src).json()
    # First attempt: analysis succeeds, then the summary call crashes the worker.
    fake = FakeClaude()
    original = fake.structured

    def crash_on_summary(**kw):
        if kw["purpose"] == "summary":
            raise SystemExit("worker killed")
        return original(**kw)

    fake.structured = crash_on_summary
    w = Worker(s, client_factory=lambda: fake)
    with pytest.raises(SystemExit):
        w.run_once()
    store = JobStore(s.db_path)
    with store._conn() as c:  # simulate the lease expiring after the crash
        c.execute("UPDATE jobs SET lease_expires_at = 0 WHERE id = ?", (job["id"],))
    second = FakeClaude()
    run_worker(s, second)
    assert "analysis" not in second.calls and "categories" not in second.calls
    assert second.calls == ["summary"]
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "completed"


def test_deleting_a_running_job_stops_the_worker(env, tmp):
    s, client = env
    src = tmp / "s.pdf"
    simple_pdf(src)
    job = upload(client, src).json()
    fake = FakeClaude()
    original = fake.structured

    def delete_midway(**kw):
        if kw["purpose"] == "analysis":
            client.delete(f"/api/jobs/{job['id']}")
        return original(**kw)

    fake.structured = delete_midway
    run_worker(s, fake)
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404
    assert not JobFiles.for_job(s.jobs_dir, job["id"]).root.exists()


def test_retention_cleanup_deletes_expired_jobs(tmp):
    s = make_settings(tmp, ANNOTATOR_RETENTION_HOURS=1)
    client = TestClient(create_app(s))
    src = tmp / "s.pdf"
    simple_pdf(src)
    job = upload(client, src).json()
    w = run_worker(s)
    store = JobStore(s.db_path)
    with store._conn() as c:
        c.execute("UPDATE jobs SET expires_at = ? WHERE id = ?", (time.time() - 1, job["id"]))
    assert w.cleanup_expired(force=True) == 1
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_eta_requires_history(tmp):
    from annotator.worker.progress import Tracker

    s = make_settings(tmp)
    store = JobStore(s.db_path)
    store.create("x" * 22, {"kind": "pdf"}, {}, 24)
    store.claim_next("w", 30, 3)
    tr = Tracker.create(store, "x" * 22, "w")
    tr.start("extract", total=10, unit="pages")
    assert tr.eta()[0] is None  # no history for AI analysis yet
    for _ in range(3):
        store.record_phase("analyze", 10, 30)
        store.record_phase("export", 1, 8)
    tr.expect("analyze", 20)
    tr.advance("extract", 5)
    eta_at, basis = tr.eta()
    assert basis == "measured" and eta_at is not None
    assert 60 - 1 <= eta_at - time.time() <= 60 + 8 + 5  # 20k tokens * 3 s/k + summary + rest of extract


def test_worker_liveness_is_reported(env, tmp):
    s, client = env
    store = JobStore(s.db_path)
    store.worker_heartbeat("w1", time.time())
    assert client.get("/api/health").json()["worker"]["alive"] is True


def test_category_suggestion_endpoint(env, tmp):
    s, client = env
    r = client.post("/api/categories/suggest", data={"instructions": "Highlight claims in yellow and data in blue."})
    assert r.status_code == 200, r.text
    assert [c["name"] for c in r.json()["categories"]] == ["Claims", "Data"]
    r = client.post("/api/categories/suggest", data={"instructions": ""})
    assert r.status_code == 400
