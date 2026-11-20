"""Config validation. Build an AppConfig from raw JSON (dict) and turn problems into user-facing messages."""

from typing import Any

from pydantic import ValidationError

from hiramedbc.config.schema import AppConfig
from hiramedbc.utils.aliases import validate_alias_name
from hiramedbc.utils.errors import ConfigError


def _format_location(loc: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in loc) or "(root)"


# Rewrite common Pydantic errors into clearer, user-facing messages
_MESSAGES = {
    "missing": "Required field",
    "extra_forbidden": "Unknown key (check the spelling)",
    "int_parsing": "Must be an integer",
    "int_type": "Must be an integer",
    "int_from_float": "Must be an integer",
    "string_type": "Must be a string",
    "dict_type": "Must be an object ({ ... })",
    "model_type": "Must be an object ({ ... })",
    "greater_than_equal": "Value is too small",
    "less_than_equal": "Value is too large",
    "string_too_short": "Must not be empty",
}


def _format_validation_error(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        if item["type"] == "value_error":
            # A ValueError raised in a field_validator is prefixed with "Value error, "
            message = item["msg"].removeprefix("Value error, ")
        elif item["type"] == "literal_error":
            message = f"Must be one of: {item.get('ctx', {}).get('expected', '')}"
        else:
            message = _MESSAGES.get(item["type"], item["msg"])
        lines.append(f"  - {_format_location(item['loc'])}: {message}")
    return "\n".join(lines)


def parse_config(raw: Any, *, source: str = "config file") -> AppConfig:
    """Validate raw data and return an AppConfig. Raise ConfigError on problems."""
    if not isinstance(raw, dict):
        raise ConfigError(f"The top level of {source} must be a JSON object.")
    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(
            f"Invalid content in {source}:\n{_format_validation_error(e)}",
            hint="Fix it with `hirame config edit`.",
        ) from None
    for name in config.connections:
        try:
            validate_alias_name(name)
        except ConfigError as e:
            raise ConfigError(f"{source}: {e.message}", hint=e.hint) from None
    return config
