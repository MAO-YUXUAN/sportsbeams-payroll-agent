from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class FileCategory:
    id: str
    label: str
    required: bool
    destination: str
    patterns: list[str]
    required_sheets: list[str] = field(default_factory=list)
    month_sheet: str | None = None
    month_check: dict | None = None


@dataclass(slots=True)
class DiscoveredFile:
    path: Path
    category_id: str | None
    category_label: str | None
    destination: str | None
    sha256: str
    matched_categories: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "name": self.path.name,
            "extension": self.path.suffix.lower(),
            "category_id": self.category_id,
            "category_label": self.category_label,
            "destination": self.destination,
            "sha256": self.sha256,
            "matched_categories": self.matched_categories,
        }
