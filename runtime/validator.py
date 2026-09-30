"""JSON Schema validation layer for incoming tool call arguments."""
from __future__ import annotations

from typing import Any


class ValidationError(ValueError):
    """Raised when LLM-provided arguments violate the function's parameter schema."""


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    """Validates arguments against JSON schema without crashing process on invalid data."""
    if not isinstance(arguments, dict):
        raise ValidationError(f"Arguments must be a JSON object, got {type(arguments).__name__}")

    # Check required fields
    required = schema.get("required", [])
    for field in required:
        if field not in arguments:
            raise ValidationError(f"Missing required parameter: '{field}'")

    # Validate types of provided properties
    properties = schema.get("properties", {})
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }

    for key, val in arguments.items():
        if key in properties:
            expected_type_str = properties[key].get("type")
            if expected_type_str and expected_type_str in type_map:
                expected_type = type_map[expected_type_str]
                # Special case: bool is subclass of int in Python
                if expected_type_str == "integer" and isinstance(val, bool):
                    raise ValidationError(
                        f"Parameter '{key}' expected integer, got boolean"
                    )
                if not isinstance(val, expected_type):
                    raise ValidationError(
                        f"Parameter '{key}' expected {expected_type_str}, got {type(val).__name__}"
                    )
