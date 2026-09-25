"""
`streams.json`: the document model (R5.1). See `document` (load, migrate, validate, save),
`streams` (stream definitions), `controls` (commands and panels), `migrate` (schema
versions) and `profile` (the device profile a file is, ADR-0010).
"""

from core.config.controls import ButtonDef, PanelDef, ParamDef, parse_commands, parse_panels
from core.config.document import (
    DEFAULT_CONFIG_PATH,
    InvalidConfigError,
    StreamConfigLoader,
    resolve_config_path,
    save_document,
    validate_config,
)
from core.config.migrate import SCHEMA_VERSION, SchemaError, migrate
from core.config.profile import Profile, ProfileEntry, list_profiles, profile_of
from core.config.streams import (
    ENDIANNESS,
    MAX_PAYLOAD_BYTES,
    Y_RANGE_MODES,
    ConfigProblem,
    validate_stream,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ENDIANNESS",
    "MAX_PAYLOAD_BYTES",
    "SCHEMA_VERSION",
    "Y_RANGE_MODES",
    "ButtonDef",
    "ConfigProblem",
    "InvalidConfigError",
    "PanelDef",
    "Profile",
    "ProfileEntry",
    "ParamDef",
    "SchemaError",
    "StreamConfigLoader",
    "list_profiles",
    "migrate",
    "parse_commands",
    "parse_panels",
    "profile_of",
    "resolve_config_path",
    "save_document",
    "validate_config",
    "validate_stream",
]
