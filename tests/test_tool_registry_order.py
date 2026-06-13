import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.tools._base import ToolDef
from lib.tools._registry import ToolRegistry


def _tool(name: str) -> ToolDef:
    return ToolDef(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {}},
    )


def _schema_names(schemas: list[dict]) -> list[str]:
    return [schema["function"]["name"] for schema in schemas]


def test_get_schemas_filters_sets_in_registration_order():
    registry = ToolRegistry()
    registry.register(_tool("b"))
    registry.register(_tool("a"))
    registry.register(_tool("c"))

    assert _schema_names(registry.get_schemas({"c", "a"})) == ["a", "c"]


def test_get_schemas_preserves_explicit_order_for_lists():
    registry = ToolRegistry()
    registry.register(_tool("b"))
    registry.register(_tool("a"))
    registry.register(_tool("c"))

    assert _schema_names(registry.get_schemas(["c", "a"])) == ["c", "a"]


def test_get_registered_order_filters_in_registration_order():
    registry = ToolRegistry()
    registry.register(_tool("b"))
    registry.register(_tool("a"))
    registry.register(_tool("c"))

    assert registry.get_registered_order({"c", "a"}) == ["a", "c"]
