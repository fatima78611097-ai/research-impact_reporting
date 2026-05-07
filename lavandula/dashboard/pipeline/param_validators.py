"""
Parameter validation and argv construction for pipeline stages.

Validates job parameters against the stage's ParamSpec definitions
and builds safe subprocess argv arrays (never shell=True, never string interpolation).
"""
from __future__ import annotations

import re

from .stages import ParamSpec, StageDefinition, STAGE_REGISTRY


US_STATES = frozenset([
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR", "VI", "GU", "AS", "MP",
])

_SHELL_METACHARACTERS = re.compile(r"[;&|`$(){}!\\\n\r\x00]")
_SAFE_STRING = re.compile(r"^[a-zA-Z0-9_./:@,\-= ]+$")


class ValidationError(Exception):
    pass


def validate_param(name: str, value, spec: ParamSpec) -> str | None:
    """Validate a single parameter value against its spec.

    Returns the cleaned string value, or None for boolean flags (presence = true).
    Raises ValidationError on invalid input.
    """
    if spec.type == "boolean":
        if value in (True, "true", "on", "1"):
            return None  # flag presence indicates true
        if value in (False, "false", "off", "0", "", None):
            return ""  # empty = don't emit flag
        raise ValidationError(f"{name}: expected boolean, got {value!r}")

    if spec.type == "state_code":
        sval = str(value).strip().upper()
        if sval not in US_STATES:
            raise ValidationError(f"{name}: '{sval}' is not a valid US state/territory code")
        return sval

    if spec.type == "integer":
        try:
            ival = int(value)
        except (TypeError, ValueError):
            raise ValidationError(f"{name}: expected integer, got {value!r}")
        if spec.min_value is not None and ival < spec.min_value:
            raise ValidationError(f"{name}: {ival} < minimum {spec.min_value}")
        if spec.max_value is not None and ival > spec.max_value:
            raise ValidationError(f"{name}: {ival} > maximum {spec.max_value}")
        return str(ival)

    if spec.type == "float":
        try:
            fval = float(value)
        except (TypeError, ValueError):
            raise ValidationError(f"{name}: expected float, got {value!r}")
        if spec.min_value is not None and fval < spec.min_value:
            raise ValidationError(f"{name}: {fval} < minimum {spec.min_value}")
        if spec.max_value is not None and fval > spec.max_value:
            raise ValidationError(f"{name}: {fval} > maximum {spec.max_value}")
        return str(fval)

    if spec.type == "choice":
        sval = str(value)
        if spec.choices is None:
            raise ValidationError(f"{name}: choice type but no choices defined")
        if sval not in spec.choices:
            raise ValidationError(f"{name}: '{sval}' not in {spec.choices}")
        return sval

    if spec.type == "string":
        sval = str(value)
        if not sval:
            raise ValidationError(f"{name}: empty string")
        if len(sval) > 200:
            raise ValidationError(f"{name}: exceeds 200 char limit ({len(sval)} chars)")
        if _SHELL_METACHARACTERS.search(sval):
            raise ValidationError(f"{name}: contains shell metacharacters")
        if spec.pattern and not re.match(spec.pattern, sval):
            raise ValidationError(f"{name}: '{sval}' does not match pattern {spec.pattern}")
        return sval

    raise ValidationError(f"{name}: unknown parameter type '{spec.type}'")


def validate_config(stage: StageDefinition, config_json: dict) -> dict[str, str | None]:
    """Validate all parameters in config_json against stage's param specs.

    Returns dict of validated {name: cleaned_value}.
    Raises ValidationError on first invalid parameter.
    """
    validated = {}

    for key in config_json:
        if key not in stage.parameters:
            raise ValidationError(
                f"Unknown parameter '{key}' for stage '{stage.name}' "
                f"(allowed: {list(stage.parameters.keys())})"
            )

    for name, spec in stage.parameters.items():
        if name in config_json:
            value = config_json[name]
            if value is None or value == "":
                if spec.required:
                    raise ValidationError(f"{name}: required parameter is empty")
                continue
            validated[name] = validate_param(name, value, spec)
        elif spec.required:
            raise ValidationError(f"{name}: required parameter missing")

    return validated


def build_argv(stage: StageDefinition, config_json: dict) -> list[str]:
    """Build a safe subprocess argv from stage command + validated parameters.

    Parameters are validated against the stage's ParamSpec before inclusion.
    Returns a list suitable for subprocess.Popen(argv, shell=False).
    """
    validated = validate_config(stage, config_json)

    argv = list(stage.command)
    for name, spec in stage.parameters.items():
        if name not in validated:
            continue
        clean_value = validated[name]
        if spec.type == "boolean":
            if clean_value is None:  # None = flag should be emitted
                argv.append(spec.cli_flag)
            # empty string = flag not emitted
        else:
            if clean_value and spec.cli_flag:
                argv.extend([spec.cli_flag, clean_value])

    return argv


def build_argv_for_phase(phase: str, config_json: dict) -> list[str]:
    """Convenience wrapper: look up stage by name and build argv."""
    if phase not in STAGE_REGISTRY:
        raise ValidationError(f"Unknown stage: '{phase}'")
    return build_argv(STAGE_REGISTRY[phase], config_json)
