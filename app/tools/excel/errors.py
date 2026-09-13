"""Excel tool exceptions."""


class ExcelToolError(RuntimeError):
    """Base error raised by the Excel tool layer."""


class ExcelDependencyError(ExcelToolError):
    """Raised when Microsoft Excel or pywin32 is unavailable."""


class ExcelInputError(ExcelToolError):
    """Raised for unsafe or invalid caller input."""


class ExcelOperationError(ExcelToolError):
    """Raised when Excel cannot complete an operation."""
