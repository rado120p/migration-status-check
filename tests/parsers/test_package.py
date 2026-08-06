"""Sjednoceni parseru - registry a jedna verze schematu."""

import pytest

from migration_validator.models.inventory import INVENTORY_SCHEMA_VERSION
from migration_validator.parsers import (
    JunosEvoAcxServiceParser,
    JunosServiceParser,
    parser_for_platform,
)
from migration_validator.parsers import core


def test_parser_for_platform():
    assert parser_for_platform("junos") is JunosServiceParser
    assert parser_for_platform("junos-evo") is JunosEvoAcxServiceParser
    with pytest.raises(ValueError, match="platform"):
        parser_for_platform("ios")


def test_single_schema_version_source():
    assert core.INVENTORY_SCHEMA_VERSION is INVENTORY_SCHEMA_VERSION
