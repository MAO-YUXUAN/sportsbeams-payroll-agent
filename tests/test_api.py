from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.jobs import JobManager


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    knowledge = root / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "file-catalog.yaml").write_text("categories:\n  - id: sample\n    label: Sample\n    required: false\n    destination: sample\n    patterns: ['*.xlsx']\n", encoding="utf-8")
    (knowledge / "llm.yaml").write_text(
        "provider: deepseek\nbase_url: https://api.deepseek.com\napi_key_env: DEEPSEEK_API_KEY\ndefault_model: deepseek-v4-flash\ncomplex_model: deepseek-v4-pro\nlimits:\n  max_requests_per_run: 1\n  max_input_tokens: 100\n  max_output_tokens: 20\n  monthly_budget_cny: 1\npricing_cny_per_million_tokens:\n  deepseek-v4-flash: {input: 1, output: 1}\n",
        encoding="utf-8",
    )
    return root


def test_health_upload_run_and_listing(tmp_path):
    app = create_app(_project(tmp_path))
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        upload = client.post("/api/v1/uploads", json={"expense_month": "2026-07"})
        assert upload.status_code == 201
        upload_id = upload.json()["upload_id"]
        saved = client.post(
            f"/api/v1/uploads/{upload_id}/files",
            files={"file": ("工资表.xlsx", b"test workbook", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert saved.status_code == 201
        assert saved.json()["size"] == 13

        created = client.post("/api/v1/runs", json={"upload_id": upload_id, "run_id": "run-test"})
        assert created.status_code == 201
        assert created.json()["state"] == "created"
        assert client.get("/api/v1/runs/run-test").status_code == 200
        listing = client.get("/api/v1/runs").json()
        assert listing["count"] == 1
        assert listing["items"][0]["run_id"] == "run-test"


def test_upload_validation_and_output_containment(tmp_path):
    app = create_app(_project(tmp_path))
    with TestClient(app) as client:
        upload_id = client.post("/api/v1/uploads", json={"expense_month": "2026-07"}).json()["upload_id"]
        bad = client.post(f"/api/v1/uploads/{upload_id}/files", files={"file": ("notes.txt", b"x", "text/plain")})
        assert bad.status_code == 400
        empty_run = client.post("/api/v1/runs", json={"upload_id": upload_id})
        assert empty_run.status_code == 400
        assert client.get("/api/v1/runs/not-found").status_code == 404


def test_rejects_non_local_host_and_untrusted_origin(tmp_path):
    app = create_app(_project(tmp_path))
    with TestClient(app) as client:
        assert client.get("/health", headers={"host": "example.com"}).status_code == 403
        response = client.post(
            "/api/v1/uploads",
            json={"expense_month": "2026-07"},
            headers={"origin": "https://evil.example"},
        )
        assert response.status_code == 403


def test_job_manager_serializes_same_run():
    manager = JobManager()
    try:
        first = manager.submit("run-1", lambda: (time.sleep(0.05) or {"ok": True}))
        try:
            manager.submit("run-1", lambda: {"ok": True})
            raise AssertionError("duplicate active job was accepted")
        except RuntimeError:
            pass
        deadline = time.time() + 2
        while manager.get(first.id).status not in {"completed", "failed"} and time.time() < deadline:
            time.sleep(0.01)
        assert manager.get(first.id).status == "completed"
        assert manager.get(first.id).result == {"ok": True}
        assert manager.get(first.id).progress == 100
    finally:
        manager.shutdown()


def test_job_manager_exposes_reported_progress():
    manager = JobManager()
    try:
        job = manager.submit("run-progress", lambda report: (report(65, "生成亚润薪资表") or {"ok": True}))
        deadline = time.time() + 2
        while manager.get(job.id).status not in {"completed", "failed"} and time.time() < deadline:
            time.sleep(0.01)
        completed = manager.get(job.id)
        assert completed.status == "completed"
        assert completed.progress == 100
        assert completed.progress_message == "处理完成"
    finally:
        manager.shutdown()


def test_home_chat_requires_task_for_run_command(tmp_path):
    app = create_app(_project(tmp_path))
    with TestClient(app) as client:
        submitted = client.post("/api/v1/chat", json={"message": "继续", "actor": "user"})
        assert submitted.status_code == 202
        job_id = submitted.json()["id"]
        deadline = time.time() + 2
        while time.time() < deadline:
            job = client.get(f"/api/v1/jobs/{job_id}").json()
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert job["status"] == "completed"
        assert "请先从首页选择一个核算任务" in job["result"]["message"]


def test_append_file_to_existing_run(tmp_path):
    root = _project(tmp_path)
    service = __import__("app.api.service", fromlist=["PayrollAPIService"]).PayrollAPIService(root)
    source = tmp_path / "source"
    source.mkdir()
    context = service.agent.create_run(expense_month="2026-07", source_directory=source, run_id="append-test")
    result = service.append_run_file(context.run_id, "new-input.xlsx", __import__("io").BytesIO(b"workbook"))
    updated = service.get_context(context.run_id)
    assert result["category"] == "sample"
    assert Path(updated.files["sample"][0]).is_file()


def test_delete_run_moves_it_to_recoverable_trash(tmp_path):
    root = _project(tmp_path)
    app = create_app(root)
    with TestClient(app) as client:
        upload_id = client.post("/api/v1/uploads", json={"expense_month": "2026-07"}).json()["upload_id"]
        client.post(f"/api/v1/uploads/{upload_id}/files", files={"file": ("input.xlsx", b"workbook", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        client.post("/api/v1/runs", json={"upload_id": upload_id, "run_id": "delete-test"})
        wrong = client.request("DELETE", "/api/v1/runs/delete-test", json={"confirm_run_id": "wrong"})
        assert wrong.status_code == 400
        deleted = client.request("DELETE", "/api/v1/runs/delete-test", json={"confirm_run_id": "delete-test"})
        assert deleted.status_code == 200
        assert deleted.json()["recoverable"] is True
        assert client.get("/api/v1/runs/delete-test").status_code == 404
        assert list((root / "data" / "trash" / "runs" / "2026-07").glob("delete-test-*"))
