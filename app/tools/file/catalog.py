from __future__ import annotations

from pathlib import Path

import yaml

from app.tools.excel.errors import ExcelInputError

from .models import FileCategory


def load_file_catalog(path: str | Path) -> list[FileCategory]:
    catalog_path = Path(path).expanduser().resolve()
    if not catalog_path.is_file():
        raise ExcelInputError(f"File catalog does not exist: {catalog_path}")
    raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    categories = []
    seen = set()
    for item in raw.get("categories", []):
        category_id = str(item["id"])
        if category_id in seen:
            raise ExcelInputError(f"Duplicate file category: {category_id}")
        seen.add(category_id)
        destination = str(item["destination"])
        destination_path = Path(destination)
        if destination_path.is_absolute() or ".." in destination_path.parts:
            raise ExcelInputError(f"Unsafe category destination: {destination}")
        categories.append(
            FileCategory(
                id=category_id,
                label=str(item["label"]),
                required=bool(item.get("required", False)),
                destination=destination,
                patterns=list(item.get("patterns", [])),
                required_sheets=list(item.get("required_sheets", [])),
                month_sheet=item.get("month_sheet"),
                month_check=item.get("month_check"),
            )
        )
    if not categories:
        raise ExcelInputError("File catalog contains no categories")
    return categories
