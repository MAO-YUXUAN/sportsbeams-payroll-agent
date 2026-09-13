from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import shutil
from datetime import datetime
from fnmatch import fnmatchcase
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.agent import PayrollAgent
from app.agent.context import AgentContext
from app.approvals import ApprovalService
from app.audit import AuditLogger
from app.security.credential_store import DEEPSEEK_CREDENTIAL_NAME, WindowsCredentialStore, get_deepseek_api_key, save_deepseek_api_key
from app.tools.excel.session import SUPPORTED_EXTENSIONS
from app.tools.excel.writer import sha256_file
from app.tools.file.catalog import load_file_catalog


SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class APIServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class PayrollAPIService:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).expanduser().resolve()
        self.agent = PayrollAgent(self.project_root)
        self.upload_root = self.project_root / "data" / "uploads"
        self.runs_root = self.project_root / "data" / "runs"
        self.trash_runs_root = self.project_root / "data" / "trash" / "runs"
        self._upload_lock = threading.RLock()

    def create_upload(self, expense_month: str) -> dict:
        upload_id = f"upl_{uuid4().hex}"
        directory = self.upload_root / upload_id
        directory.mkdir(parents=True, exist_ok=False)
        metadata = {"upload_id": upload_id, "expense_month": expense_month, "files": []}
        self._write_metadata(directory, metadata)
        return metadata

    def save_upload(self, upload_id: str, filename: str, stream: BinaryIO) -> dict:
        with self._upload_lock:
            return self._save_upload_locked(upload_id, filename, stream)

    def _save_upload_locked(self, upload_id: str, filename: str, stream: BinaryIO) -> dict:
        directory, metadata = self._load_upload(upload_id)
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name or safe_name.startswith((".", "~$")):
            raise APIServiceError("Unsafe or unsupported filename")
        if Path(safe_name).suffix.casefold() not in SUPPORTED_EXTENSIONS:
            raise APIServiceError("Only Excel workbook files are accepted")
        target = directory / safe_name
        if target.exists():
            raise APIServiceError(f"File already uploaded: {safe_name}", 409)
        size = 0
        try:
            with target.open("xb") as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise APIServiceError("File exceeds the 50 MB upload limit", 413)
                    output.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        metadata["files"].append({"name": safe_name, "size": size})
        self._write_metadata(directory, metadata)
        return {"upload_id": upload_id, "name": safe_name, "size": size}

    def get_upload(self, upload_id: str) -> dict:
        return self._load_upload(upload_id)[1]

    def create_run(self, upload_id: str, run_id: str | None = None) -> AgentContext:
        directory, metadata = self._load_upload(upload_id)
        if not metadata["files"]:
            raise APIServiceError("Upload session contains no files")
        return self.agent.create_run(expense_month=metadata["expense_month"], source_directory=directory, run_id=run_id)

    def append_run_file(self, run_id: str, filename: str, stream: BinaryIO) -> dict:
        context = self.get_context(run_id)
        if context.state in {"completed", "failed"}:
            raise APIServiceError(f"Files cannot be added while run is {context.state}", 409)
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name or safe_name.startswith((".", "~$")):
            raise APIServiceError("Unsafe or unsupported filename")
        if Path(safe_name).suffix.casefold() not in SUPPORTED_EXTENSIONS:
            raise APIServiceError("Only Excel workbook files are accepted")
        matches = [
            category for category in load_file_catalog(self.agent.file_catalog)
            if any(fnmatchcase(safe_name.casefold(), pattern.casefold()) for pattern in category.patterns)
        ]
        if len(matches) != 1:
            raise APIServiceError("The file could not be classified uniquely", 422)
        category = matches[0]
        if category.id in {"yicai_payroll", "yarun_payroll"}:
            raise APIServiceError(f"{category.label}由系统从主工资表生成，无需上传", 422)
        if context.files.get(category.id):
            raise APIServiceError(f"A file for {category.label} has already been received", 409)
        input_root = (Path(context.run_directory) / "input").resolve()
        destination_directory = (input_root / category.destination).resolve()
        if input_root not in destination_directory.parents:
            raise APIServiceError("Unsafe category destination", 500)
        destination_directory.mkdir(parents=True, exist_ok=True)
        destination = destination_directory / safe_name
        size = 0
        try:
            with destination.open("xb") as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise APIServiceError("File exceeds the 50 MB upload limit", 413)
                    output.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        context.files[category.id] = [str(destination)]
        context.summaries["required_files"] = self.agent.input_requirements(context)
        context.save()
        AuditLogger(context.run_directory, run_id=context.run_id).log(
            "run_file_received", actor="user", status="success",
            details={"category": category.id, "filename": safe_name, "size": size, "sha256": sha256_file(destination)},
        )
        return {"run_id": run_id, "category": category.id, "category_label": category.label, "name": safe_name, "size": size}

    def list_runs(self) -> list[dict]:
        if not self.runs_root.exists():
            return []
        contexts = []
        for state_path in self.runs_root.glob("*/*/state.json"):
            try:
                contexts.append(asdict(AgentContext.load(state_path.parent)))
            except Exception:
                continue
        return sorted(contexts, key=lambda item: item["created_at"], reverse=True)

    def get_context(self, run_id: str) -> AgentContext:
        self._validate_id(run_id, "run_id")
        matches = list(self.runs_root.glob(f"*/{run_id}/state.json"))
        if not matches:
            raise APIServiceError(f"Run not found: {run_id}", 404)
        if len(matches) > 1:
            raise APIServiceError(f"Run ID is not unique: {run_id}", 409)
        context = AgentContext.load(matches[0].parent)
        self.agent.sync_agent_notifications(context)
        if context.state in {"waiting_additional_inputs", "waiting_vendor_returns"}:
            current = self.agent.input_requirements(context)
            if context.summaries.get("required_files") != current:
                context.summaries["required_files"] = current
                context.save()
        return context

    def delete_run(self, run_id: str, *, confirm_run_id: str) -> dict:
        if confirm_run_id != run_id:
            raise APIServiceError("Run deletion confirmation does not match", 400)
        context = self.get_context(run_id)
        source = Path(context.run_directory).resolve()
        runs_root = self.runs_root.resolve()
        if runs_root not in source.parents or not source.is_dir():
            raise APIServiceError("Unsafe run deletion target", 500)
        AuditLogger(source, run_id=run_id).log(
            "run_moved_to_trash", actor="user", status="success",
            details={"original_path": str(source)},
        )
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        destination_root = (self.trash_runs_root / context.expense_month).resolve()
        trash_root = self.trash_runs_root.resolve()
        if trash_root != destination_root and trash_root not in destination_root.parents:
            raise APIServiceError("Unsafe trash destination", 500)
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / f"{run_id}-{timestamp}"
        if destination.exists():
            raise APIServiceError("A trash entry with the same name already exists", 409)
        shutil.move(str(source), str(destination))
        return {"run_id": run_id, "deleted": True, "recoverable": True, "trash_path": str(destination)}

    def approvals(self, context: AgentContext) -> list[dict]:
        return [item.to_dict() for item in ApprovalService(context.run_directory).list()]

    def audit_events(self, context: AgentContext) -> list[dict]:
        return [item.to_dict() for item in AuditLogger(context.run_directory, run_id=context.run_id).read()]

    def output_files(self, context: AgentContext) -> list[dict]:
        root = (Path(context.run_directory) / "output").resolve()
        if not root.exists():
            return []
        return [
            {"name": path.name, "relative_path": path.relative_to(root).as_posix(), "size": path.stat().st_size}
            for path in sorted(root.rglob("*")) if path.is_file()
        ]

    def output_path(self, context: AgentContext, relative_path: str) -> Path:
        root = (Path(context.run_directory) / "output").resolve()
        target = (root / relative_path).resolve()
        if target == root or root not in target.parents or not target.is_file():
            raise APIServiceError("Output file not found", 404)
        return target

    @staticmethod
    def llm_status() -> dict:
        return {"provider": "deepseek", "configured": bool(get_deepseek_api_key())}

    @staticmethod
    def save_llm_key(api_key: str) -> None:
        save_deepseek_api_key(api_key)

    @staticmethod
    def delete_llm_key() -> bool:
        return WindowsCredentialStore().delete(DEEPSEEK_CREDENTIAL_NAME)

    def _load_upload(self, upload_id: str) -> tuple[Path, dict]:
        self._validate_id(upload_id, "upload_id")
        directory = (self.upload_root / upload_id).resolve()
        if self.upload_root.resolve() not in directory.parents or not directory.is_dir():
            raise APIServiceError(f"Upload session not found: {upload_id}", 404)
        metadata_path = directory / "upload.json"
        try:
            return directory, json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as error:
            raise APIServiceError("Upload session metadata is invalid", 500) from error

    @staticmethod
    def _write_metadata(directory: Path, metadata: dict) -> None:
        target = directory / "upload.json"
        handle, temporary_name = tempfile.mkstemp(prefix="upload-", suffix=".tmp", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(metadata, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate_id(value: str, name: str) -> None:
        if not SAFE_ID.fullmatch(value):
            raise APIServiceError(f"Invalid {name}")
