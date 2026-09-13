from app.tools.excel.models import ToolResult


def test_tool_result_is_serializable() -> None:
    result = ToolResult(success=True, operation="example", data={"value": 1})
    assert result.to_dict() == {
        "success": True,
        "operation": "example",
        "data": {"value": 1},
        "warnings": [],
        "errors": [],
    }
