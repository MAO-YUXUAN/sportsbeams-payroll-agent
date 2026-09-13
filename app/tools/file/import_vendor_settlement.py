from __future__ import annotations

import shutil
from pathlib import Path

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import resolve_excel_path
from app.tools.excel.writer import sha256_file


class VendorSettlementFileTool:
    """Import a vendor settlement workbook into a month-specific input directory."""

    def import_file(
        self,
        source_path: str | Path,
        input_directory: str | Path,
        *,
        vendor: str,
        expense_month: str,
    ) -> ToolResult[dict]:
        source = resolve_excel_path(source_path)
        if not vendor.strip():
            raise ExcelInputError("Vendor is required")
        if not expense_month.strip():
            raise ExcelInputError("Expense month is required")

        destination_dir = Path(input_directory).expanduser().resolve()
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / source.name
        source_hash = sha256_file(source)

        if destination.exists():
            if sha256_file(destination) == source_hash:
                return ToolResult(
                    True,
                    "import_vendor_settlement",
                    {
                        "status": "duplicate",
                        "vendor": vendor,
                        "expense_month": expense_month,
                        "source_path": str(source),
                        "stored_path": str(destination),
                        "sha256": source_hash,
                    },
                    ["The same file has already been imported."],
                )
            raise ExcelInputError(f"A different file already uses this name: {destination.name}")

        shutil.copy2(source, destination)
        if sha256_file(destination) != source_hash:
            destination.unlink(missing_ok=True)
            raise ExcelInputError("Imported file hash does not match the source")

        return ToolResult(
            True,
            "import_vendor_settlement",
            {
                "status": "accepted",
                "vendor": vendor,
                "expense_month": expense_month,
                "source_path": str(source),
                "stored_path": str(destination),
                "sha256": source_hash,
            },
        )
