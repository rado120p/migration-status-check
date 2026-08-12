"""Vyroba inventory do run adresare (capture --parse-services)."""

from __future__ import annotations

from pathlib import Path

from migration_validator.parsers import (
    create_yaml_data,
    parser_for_platform,
    retrieve_configuration,
    write_yaml,
)


def generate_inventory(
    device,
    platform: str,
    output_path: Path,
    port: str | None = None,
) -> None:
    """Stahne konfiguraci pres otevrene PyEZ spojeni a zapise inventory YAML.

    Pri zadanem `port` se ponechaji jen sluzby, jejichz fyzicke jmeno
    rozhrani (cast pred teckou) odpovida.
    """

    parser_cls = parser_for_platform(platform)
    config = retrieve_configuration(
        device,
        hierarchies=parser_cls.CONFIG_HIERARCHIES,
    )
    services = parser_cls(config).parse()

    if port is not None:
        services = [
            service
            for service in services
            if service.interface.split(".", 1)[0] == port
        ]

    write_yaml(
        create_yaml_data(_hostname(device), services),
        output_path,
    )


def _hostname(device) -> str:
    return getattr(device, "hostname", None) or ""
