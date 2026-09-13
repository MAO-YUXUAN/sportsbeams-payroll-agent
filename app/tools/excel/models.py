from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Generic, TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class CellData:
    sheet: str
    address: str
    value: Any
    display_value: str
    formula: str | None = None


@dataclass(slots=True)
class SheetInspection:
    name: str
    index: int
    visibility: str
    used_range: str
    rows: int
    columns: int
    formula_cells: int
    shape_count: int
    table_count: int


@dataclass(slots=True)
class WorkbookInspection:
    path: str
    extension: str
    sheet_count: int
    sheets: list[SheetInspection]
    external_links: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FormulaChange:
    sheet: str
    address: str
    old_formula: str
    new_formula: str
    reason: str


@dataclass(slots=True)
class WorkbookValidation:
    path: str
    valid: bool
    required_sheets_missing: list[str] = field(default_factory=list)
    formula_errors: list[CellData] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolResult(Generic[T]):
    success: bool
    operation: str
    data: T | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def json_safe(value: Any) -> Any:
    """Convert common COM values into JSON-safe values without changing numbers."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    return str(value)
