"""Keep the example's monitoring APIs behind their documented authentication."""

from pathlib import Path
import re

import pytest
import yaml


EXAMPLE = Path(__file__).resolve().parents[2] / "example_project/outer_rim_logistics"


@pytest.mark.parametrize("endpoint", ["prometheus", "loki"])
def test_monitoring_proxy_requires_authentication_in_every_virtual_host(
    endpoint: str,
) -> None:
    config = (EXAMPLE / "docker/nginx.conf").read_text()
    locations = re.findall(rf"location /{endpoint}/\s*\{{([^}}]+)\}}", config)
    assert len(locations) == 2
    for location in locations:
        assert 'auth_basic "Observability";' in location
        assert "auth_basic_user_file /etc/nginx/htpasswd/observability;" in location
        assert 'proxy_set_header Authorization "";' in location
        assert "satisfy any" not in location


def test_monitoring_password_file_is_required_read_only_and_not_published() -> None:
    services = yaml.safe_load((EXAMPLE / "docker-compose.yml").read_text())["services"]
    mount = next(
        (
            volume
            for volume in services["nginx"]["volumes"]
            if isinstance(volume, dict)
            and volume.get("target") == "/etc/nginx/htpasswd/observability"
        ),
        None,
    )
    assert mount is not None
    assert mount["source"] == "./docker/htpasswd/observability"
    assert mount["type"] == "bind"
    assert mount["read_only"] is True
    assert mount["bind"]["create_host_path"] is False
    for service in ("loki", "prometheus"):
        assert not services[service].get("ports")
