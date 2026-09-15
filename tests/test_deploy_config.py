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


def test_backup_timer_is_wired_to_an_installed_script() -> None:
    service = (DEPLOY / "agntspark-backup.service").read_text()
    timer = (DEPLOY / "agntspark-backup.timer").read_text()
    bootstrap = (DEPLOY / "bootstrap.sh").read_text()

    assert "/opt/agntspark/agntspark-gateway/deploy/backup.sh" in service
    assert (DEPLOY / "backup.sh").is_file()
    assert "OnCalendar=" in timer
    assert "Persistent=true" in timer
    for unit in ("agntspark-backup.service", "agntspark-backup.timer"):
        assert f'"$DEPLOY/{unit}" /etc/systemd/system/{unit}' in bootstrap
    assert "systemctl enable --now agntspark-backup.timer" in bootstrap


def test_deploy_script_is_read_before_it_updates_itself() -> None:
    # deploy.sh resets the checkout it lives in; bash reads scripts as it
    # goes, so everything must run from a function defined before the reset.
    script = (DEPLOY / "deploy.sh").read_text()
    body = script[script.index("main() {") :]
    assert "git -C" in body
    assert script.rstrip().endswith('main "$@"')
    assert "bootstrap.sh" in body

    workflow = (DEPLOY.parent / ".github" / "workflows" / "deploy.yml").read_text()
    assert "deploy/deploy.sh" in workflow


def test_agent_ingress_sets_the_client_ip_itself() -> None:
    # The gateway rate limits agent callers by this header, so a visitor must
    # not be able to supply their own.
    caddyfile = (DEPLOY / "Caddyfile").read_text()
    agents_site = caddyfile[caddyfile.index("*.{$AGENT_DOMAIN}") :]
    assert "request_header -X-Agnt-Client-IP" in agents_site
    assert "header_up X-Agnt-Client-IP {client_ip}" in agents_site
    assert agents_site.index("request_header -X-Agnt-Client-IP") < agents_site.index("forward_auth")


def test_caddy_address_is_the_one_isolation_allows() -> None:
    compose = (DEPLOY / "docker-compose.prod.yml").read_text()
    caddy_ip = re.search(r"ipv4_address:\s*(\S+)", compose)
    assert caddy_ip
    unit = (DEPLOY / "agntspark-isolate-agents.service").read_text()
    assert set(re.findall(r"-[sd] (\S+)", unit)) == {caddy_ip.group(1)}
