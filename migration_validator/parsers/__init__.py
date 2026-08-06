"""Sjednocene parsery konfigurace (MX + EVO/ACX), spolecne jadro v core."""

from migration_validator.parsers.core import (
    create_yaml_data,
    retrieve_configuration,
    write_yaml,
)
from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

_PLATFORM_PARSERS = {
    "junos": JunosServiceParser,
    "junos-evo": JunosEvoAcxServiceParser,
}


def parser_for_platform(platform: str):
    try:
        return _PLATFORM_PARSERS[platform]
    except KeyError:
        raise ValueError(f"nepodporovana platforma parseru: {platform}") from None
