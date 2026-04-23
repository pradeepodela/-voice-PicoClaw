from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Optional, Union


class ToolDefinition:
    def __init__(
        self,
        func: Callable,
        name: str,
        description: str,
        parameters: dict,
    ):
        self.func = func
        self.name = name
        self.description = description
        self.parameters = parameters

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, **kwargs) -> str:
        if inspect.iscoroutinefunction(self.func):
            result = await self.func(**kwargs)
        else:
            result = self.func(**kwargs)
        return str(result)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool_def: ToolDefinition) -> None:
        self._tools[tool_def.name] = tool_def

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def all_schemas(self) -> list[dict]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    async def call(self, name: str, arguments: str | dict) -> str:
        tool = self.get(name)
        if not tool:
            return f"Error: tool '{name}' not found"
        if isinstance(arguments, str):
            try:
                kwargs = json.loads(arguments)
            except json.JSONDecodeError as e:
                return f"Error: invalid tool arguments JSON: {e}"
        else:
            kwargs = arguments
        try:
            return await tool.execute(**kwargs)
        except Exception as e:
            return f"Error executing tool '{name}': {e}"


# Module-level registry instance
_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _registry


def tool(
    name: str | None = None,
    description: str = "",
    parameters: dict | None = None,
):
    """
    Decorator to register a function as a callable tool.

    Usage:
        @tool(name="calculator", description="Evaluate a math expression")
        def calculate(expression: str) -> str:
            ...

    The `parameters` dict follows JSON Schema format. If omitted, a basic
    schema is inferred from the function signature (strings only).
    """
    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        tool_desc = description or (inspect.getdoc(func) or "")

        if parameters is not None:
            schema = parameters
        else:
            schema = _infer_schema(func)

        tool_def = ToolDefinition(
            func=func,
            name=tool_name,
            description=tool_desc,
            parameters=schema,
        )
        _registry.register(tool_def)
        return func

    return decorator


def _infer_schema(func: Callable) -> dict:
    """Build a basic JSON Schema from a function's type annotations."""
    sig = inspect.signature(func)
    props: dict[str, Any] = {}
    required: list[str] = []

    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
    }

    for param_name, param in sig.parameters.items():
        annotation = param.annotation
        json_type = type_map.get(annotation, "string")
        props[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "object",
        "properties": props,
        "required": required,
    }
