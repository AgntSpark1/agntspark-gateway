"""Static checks on deploy/ that would otherwise only fail on the server."""

from __future__ import annotations

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def _bridge_name() -> str:
    compose = (DEPLOY / "docker-compose.prod.yml").read_text()
    match = re.search(r"com\.docker\.network\.bridge\.name:\s*(\S+)", compose)
    assert match, "agents network must pin a bridge name"
    return match.group(1)


def test_bridge_name_fits_linux_interface_limit() -> None:
    # IFNAMSIZ is 16 including the NUL terminator: Docker fails to create the
    # network and iptables rejects the rule beyond 15 characters.
    assert len(_bridge_name()) <= 15


def test_isolation_rules_and_bootstrap_use_the_same_bridge() -> None:
    name = _bridge_name()
    unit = (DEPLOY / "agntspark-isolate-agents.service").read_text()
    interfaces = set(re.findall(r"-[io] (\S+)", unit))
    assert interfaces == {name}
    assert f'!= "{name}"' in (DEPLOY / "bootstrap.sh").read_text()


def test_isolation_enables_bridge_netfilter_first() -> None:
    # Same-bridge container traffic skips iptables entirely unless this is on;
    # the first production deploy shipped rules that matched nothing.
    unit = (DEPLOY / "agntspark-isolate-agents.service").read_text()
    assert "ExecStartPre=/sbin/modprobe br_netfilter" in unit
    assert "net.bridge.bridge-nf-call-iptables=1" in unit
    assert unit.index("ExecStartPre=") < unit.index("ExecStart=")


def test_caddy_address_is_the_one_isolation_allows() -> None:
    compose = (DEPLOY / "docker-compose.prod.yml").read_text()
    caddy_ip = re.search(r"ipv4_address:\s*(\S+)", compose)
    assert caddy_ip
    unit = (DEPLOY / "agntspark-isolate-agents.service").read_text()
    assert set(re.findall(r"-[sd] (\S+)", unit)) == {caddy_ip.group(1)}
