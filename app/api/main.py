from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent import IntentRouter, PayrollConversation, PayrollExplanationClient, load_llm_config
from app.agent import WorkflowStateError
from app.agent.llm_usage import LLMUsageLedger
from app.approvals import ApprovalError
from app.tools.excel.errors import ExcelInputError, ExcelOperationError

from .jobs import JobManager
from .schemas import APIKeyUpdate, ApprovalDecision, ChatRequest, RunCreate, RunDelete, UploadSessionCreate
from .service import APIServiceError, PayrollAPIService


def create_app(project_root: str | Path | None = None) -> FastAPI:
    root = Path(project_root or os.getenv("SPORTSBEAMS_PROJECT_ROOT") or Path(__file__).parents[2]).resolve()
    service = PayrollAPIService(root)
    jobs = JobManager()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        jobs.shutdown()

    api = FastAPI(
        title="Sportsbeams Payroll Agent API",
        version="1.0.0",
        description="Local API for controlled payroll processing, approvals and output delivery.",
        lifespan=lifespan,
    )
    api.state.service = service
    api.state.jobs = jobs
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1", "http://localhost", "http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8765", "http://localhost:8765"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )
    trusted_origins = {
        "http://127.0.0.1:5173", "http://localhost:5173",
        "http://127.0.0.1:8765", "http://localhost:8765",
    }

    @api.middleware("http")
    async def local_request_guard(request, call_next):
        host = request.headers.get("host", "").split(":", 1)[0].casefold()
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse(status_code=403, content={"detail": "This service only accepts local requests"})
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in trusted_origins:
            return JSONResponse(status_code=403, content={"detail": "Untrusted request origin"})
        return await call_next(request)

    @api.exception_handler(APIServiceError)
    async def api_service_error(_, error: APIServiceError):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=error.status_code, content={"detail": str(error)})

    @api.exception_handler(ApprovalError)
    @api.exception_handler(WorkflowStateError)
    async def conflict_error(_, error: Exception):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.exception_handler(ExcelInputError)
    @api.exception_handler(ExcelOperationError)
    async def payroll_input_error(_, error: Exception):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @api.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "sportsbeams-payroll-agent", "version": api.version}

    @api.get("/api/v1/system/readiness")
    def readiness() -> dict:
        return {
            "ready": (root / "knowledge" / "file-catalog.yaml").is_file(),
            "project_root": str(root),
            "llm": service.llm_status(),
        }

    @api.post("/api/v1/uploads", status_code=status.HTTP_201_CREATED)
    def create_upload(request: UploadSessionCreate) -> dict:
        return service.create_upload(request.expense_month)

    @api.get("/api/v1/uploads/{upload_id}")
    def get_upload(upload_id: str) -> dict:
        return service.get_upload(upload_id)

    @api.post("/api/v1/uploads/{upload_id}/files", status_code=status.HTTP_201_CREATED)
    def upload_file(upload_id: str, file: UploadFile = File(...)) -> dict:
        if not file.filename:
            raise HTTPException(400, "Filename is required")
        try:
            return service.save_upload(upload_id, file.filename, file.file)
        finally:
            file.file.close()

    @api.post("/api/v1/runs", status_code=status.HTTP_201_CREATED)
    def create_run(request: RunCreate) -> dict:
        return asdict(service.create_run(request.upload_id, request.run_id))

    @api.get("/api/v1/runs")
    def list_runs() -> dict:
        values = service.list_runs()
        return {"items": values, "count": len(values)}

    @api.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return asdict(service.get_context(run_id))

    @api.delete("/api/v1/runs/{run_id}")
    def delete_run(run_id: str, request: RunDelete) -> dict:
        if jobs.is_active(run_id):
            raise HTTPException(409, "Run has an active job and cannot be deleted")
        return service.delete_run(run_id, confirm_run_id=request.confirm_run_id)

    @api.post("/api/v1/runs/{run_id}/advance", status_code=status.HTTP_202_ACCEPTED)
    def advance_run(run_id: str) -> dict:
        service.get_context(run_id)

        def operation(report) -> dict:
            context = service.get_context(run_id)
            return asdict(service.agent.advance(context, progress=report))

        try:
            return jobs.submit(run_id, operation).to_dict()
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error

    @api.post("/api/v1/runs/{run_id}/files", status_code=status.HTTP_201_CREATED)
    def append_run_file(run_id: str, file: UploadFile = File(...)) -> dict:
        if not file.filename:
            raise HTTPException(400, "Filename is required")
        try:
            return service.append_run_file(run_id, file.filename, file.file)
        finally:
            file.file.close()

    @api.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job.to_dict()

    @api.get("/api/v1/runs/{run_id}/approvals")
    def list_approvals(run_id: str) -> dict:
        items = service.approvals(service.get_context(run_id))
        return {"items": items, "count": len(items)}

    @api.post("/api/v1/runs/{run_id}/approvals/{approval_id}/decision")
    def decide_approval(run_id: str, approval_id: str, request: ApprovalDecision) -> dict:
        context = service.get_context(run_id)
        if request.decision == "approve":
            record = service.agent.approve(context, approval_id, decided_by=request.decided_by, comment=request.comment)
        else:
            record = service.agent.reject(context, approval_id, decided_by=request.decided_by, comment=request.comment)
        return record.to_dict()

    @api.get("/api/v1/runs/{run_id}/outputs")
    def list_outputs(run_id: str) -> dict:
        items = service.output_files(service.get_context(run_id))
        return {"items": items, "count": len(items)}

    @api.get("/api/v1/runs/{run_id}/outputs/{relative_path:path}")
    def download_output(run_id: str, relative_path: str):
        path = service.output_path(service.get_context(run_id), relative_path)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @api.get("/api/v1/runs/{run_id}/audit")
    def get_audit(run_id: str) -> dict:
        context = service.get_context(run_id)
        logger = __import__("app.audit", fromlist=["AuditLogger"]).AuditLogger(context.run_directory, run_id=context.run_id)
        items = [item.to_dict() for item in logger.read()]
        verification = logger.verify()
        return {"items": items, "count": len(items), "verification": asdict(verification)}

    @api.post("/api/v1/runs/{run_id}/chat", status_code=status.HTTP_202_ACCEPTED)
    def chat(run_id: str, request: ChatRequest) -> dict:
        service.get_context(run_id)

        def operation(report) -> dict:
            report(15, "理解问题并读取当前任务")
            context = service.get_context(run_id)
            config = load_llm_config(root / "knowledge" / "llm.yaml")
            ledger = LLMUsageLedger(root / "data" / "llm-usage.jsonl", run_id=context.run_id)
            conversation = PayrollConversation(service.agent, llm=PayrollExplanationClient(model=request.model, config=config, usage_ledger=ledger))
            result = conversation.handle(context, request.message, actor=request.actor)
            report(90, "整理回答")
            return result

        try:
            return jobs.submit(run_id, operation).to_dict()
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error

    @api.post("/api/v1/chat", status_code=status.HTTP_202_ACCEPTED)
    def home_chat(request: ChatRequest) -> dict:
        """Read-only assistant for the home page; never executes run commands."""
        def operation(report) -> dict:
            report(15, "读取任务概览")
            runs = service.list_runs()
            overview = [
                {"run_id": item["run_id"], "expense_month": item["expense_month"], "state": item["state"], "updated_at": item["updated_at"]}
                for item in runs[:20]
            ]
            intent = IntentRouter().route(request.message)
            if intent.name in {"advance", "approve", "reject", "repair_current_run", "status", "missing_files", "list_approvals", "list_outputs"}:
                report(90, "整理回答")
                return {
                    "intent": {"name": "home", "parameters": {}, "source": "local"},
                    "message": "请先从首页选择一个核算任务，再执行状态查询、审批或流程操作。",
                    "data": {"runs": overview},
                }
            config = load_llm_config(root / "knowledge" / "llm.yaml")
            ledger = LLMUsageLedger(root / "data" / "llm-usage.jsonl", run_id="home")
            llm = PayrollExplanationClient(model=request.model, config=config, usage_ledger=ledger)
            if not llm.api_key:
                message = "DeepSeek当前不可用，可能是账户余额不足、网络异常或API配置问题。任务创建和工资核算仍可正常使用。"
            else:
                try:
                    message = llm.explain(
                        {"scope": "home", "task_count": len(runs), "tasks": overview},
                        question=request.message,
                    )
                except Exception:
                    message = "DeepSeek当前不可用，可能是账户余额不足、网络异常或API配置问题。任务创建和工资核算仍可正常使用。"
            report(90, "整理回答")
            return {"intent": {"name": "chat", "parameters": {"question": request.message}, "source": "llm"}, "message": message, "data": {"runs": overview}}

        try:
            return jobs.submit("__home__", operation).to_dict()
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error

    @api.get("/api/v1/settings/llm")
    def llm_status() -> dict:
        return service.llm_status()

    @api.put("/api/v1/settings/llm")
    def update_llm_key(request: APIKeyUpdate) -> dict:
        service.save_llm_key(request.api_key)
        return {"provider": "deepseek", "configured": True}

    @api.delete("/api/v1/settings/llm")
    def delete_llm_key() -> dict:
        deleted = service.delete_llm_key()
        return {**service.llm_status(), "deleted": deleted}

    frontend_dist = root / "frontend" / "dist"
    if frontend_dist.is_dir():
        api.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return api


app = create_app()
