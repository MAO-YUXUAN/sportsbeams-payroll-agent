from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

from app.tools.excel.errors import ExcelInputError
from app.tools.excel.models import ToolResult
from app.tools.excel.session import SUPPORTED_EXTENSIONS
from app.tools.excel.writer import sha256_file

from .catalog import load_file_catalog
from .models import DiscoveredFile


class FileDiscoveryTool:
    """Discover and classify Excel inputs without opening or modifying them."""

    def discover(
        self,
        source_directory: str | Path,
        catalog_path: str | Path,
        *,
        recursive: bool = False,
    ) -> ToolResult[dict]:
        source = Path(source_directory).expanduser().resolve()
        if not source.is_dir():
            raise ExcelInputError(f"Input directory does not exist: {source}")
        categories = load_file_catalog(catalog_path)
        iterator = source.rglob("*") if recursive else source.iterdir()
        files = sorted(
            (path for path in iterator if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS),
            key=lambda path: path.name.casefold(),
        )
        discovered: list[DiscoveredFile] = []
        for path in files:
            matches = [
                category
                for category in categories
                if any(fnmatchcase(path.name.casefold(), pattern.casefold()) for pattern in category.patterns)
            ]
            selected = matches[0] if len(matches) == 1 else None
            discovered.append(
                DiscoveredFile(
                    path=path.resolve(),
                    category_id=selected.id if selected else None,
                    category_label=selected.label if selected else None,
                    destination=selected.destination if selected else None,
                    sha256=sha256_file(path),
                    matched_categories=[match.id for match in matches],
                )
            )
        ambiguous = [item.to_dict() for item in discovered if len(item.matched_categories) > 1]
        unclassified = [item.to_dict() for item in discovered if not item.matched_categories]
        return ToolResult(
            not ambiguous,
            "discover_files",
            {
                "source_directory": str(source),
                "files": [item.to_dict() for item in discovered],
                "classified_count": sum(item.category_id is not None for item in discovered),
                "unclassified": unclassified,
                "ambiguous": ambiguous,
            },
            [f"{len(unclassified)} Excel file(s) were not classified."] if unclassified else [],
        )
