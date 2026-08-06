#!/usr/bin/env python3
"""Tenky wrapper - implementace zije v migration_validator/parsers/."""

import sys

from migration_validator.parsers.core import main
from migration_validator.parsers.mx import JunosServiceParser

if __name__ == "__main__":
    sys.exit(main(parser_cls=JunosServiceParser))
