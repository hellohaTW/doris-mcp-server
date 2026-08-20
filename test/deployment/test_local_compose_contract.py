# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_DIRECTORY = REPOSITORY_ROOT / "deploy" / "local"
LOCAL_COMPOSE_PATH = LOCAL_DIRECTORY / "docker-compose.yml"
LOCAL_ENV_EXAMPLE_PATH = LOCAL_DIRECTORY / ".env.example"
LOCAL_SECRETS_IGNORE_PATH = LOCAL_DIRECTORY / "secrets" / ".gitignore"
PINNED_IMAGES = {
    "doris-fe": (
        "apache/doris:fe-3.0.8@"
        "sha256:749afa2be75cd58d54a0bdb3edaa805d5974646f67500f094912f8dbc4a96b8f"
    ),
    "doris-be": (
        "apache/doris:be-3.0.8@"
        "sha256:a337e2b17a65c86c22a1205467a825d46dc3380c94ba8024e53eea033641f12e"
    ),
}


def _compose() -> dict:
    return yaml.safe_load(LOCAL_COMPOSE_PATH.read_text(encoding="utf-8"))


def test_local_compose_builds_the_repository_dockerfile() -> None:
    service = _compose()["services"]["doris-mcp-server"]

    assert service["build"] == {"context": "../..", "dockerfile": "Dockerfile"}
    assert ":latest" not in service["image"]


def test_local_mcp_listener_is_published_on_loopback_only() -> None:
    service = _compose()["services"]["doris-mcp-server"]

    assert service["ports"] == [
        {
            "target": 3000,
            "published": "${MCP_HTTP_PORT:-3000}",
            "host_ip": "127.0.0.1",
            "protocol": "tcp",
        }
    ]
    assert "SERVER_HOST=0.0.0.0" in service["environment"]
    assert "SERVER_PORT=3000" in service["environment"]
    assert service["healthcheck"]["test"] == [
        "CMD",
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        "3",
        "http://127.0.0.1:3000/ready",
    ]


def test_local_service_keeps_server_logs_visible() -> None:
    """Console logging is suppressed unless stdout is a tty, and a bind-mounted
    log directory is unwritable to the image's non-root account."""
    service = _compose()["services"]["doris-mcp-server"]

    assert service["tty"] is True
    assert "volumes" not in service
    assert not any(
        entry.startswith("LOG_FILE_PATH=") for entry in service["environment"]
    )


def test_local_non_loopback_bind_keeps_an_authentication_boundary() -> None:
    """A 0.0.0.0 bind must never rely on the dangerous unauthenticated override."""
    environment = _compose()["services"]["doris-mcp-server"]["environment"]
    compose_text = LOCAL_COMPOSE_PATH.read_text(encoding="utf-8")

    assert "ENABLE_TOKEN_AUTH=true" in environment
    assert "TOKEN_ADMIN_FILE=/run/secrets/mcp_static_token" in environment
    assert "ALLOW_UNAUTHENTICATED_NON_LOOPBACK" not in compose_text


def test_local_transport_allowlists_are_explicit_for_a_non_loopback_bind() -> None:
    environment = _compose()["services"]["doris-mcp-server"]["environment"]

    assert (
        "MCP_ALLOWED_HOSTS=${MCP_ALLOWED_HOSTS:-127.0.0.1:*,localhost:*,[::1]:*}"
        in environment
    )
    assert (
        "MCP_ALLOWED_ORIGINS=${MCP_ALLOWED_ORIGINS:"
        "-http://127.0.0.1:*,http://localhost:*}"
    ) in environment


def test_local_credentials_are_file_backed_secrets() -> None:
    compose = _compose()
    service = compose["services"]["doris-mcp-server"]

    assert compose["secrets"] == {
        "doris_password": {
            "file": "${LOCAL_DORIS_PASSWORD_FILE:-./secrets/doris_password}"
        },
        "mcp_static_token": {
            "file": "${LOCAL_MCP_STATIC_TOKEN_FILE:-./secrets/mcp_static_token}"
        },
    }
    assert service["secrets"] == ["doris_password", "mcp_static_token"]
    assert "DORIS_PASSWORD_FILE=/run/secrets/doris_password" in service["environment"]
    assert not any(
        entry.startswith(("DORIS_PASSWORD=", "TOKEN_ADMIN="))
        for entry in service["environment"]
    )


def test_local_compose_ships_no_credential_material() -> None:
    ignored = LOCAL_SECRETS_IGNORE_PATH.read_text(encoding="utf-8").splitlines()
    tracked_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (LOCAL_COMPOSE_PATH, LOCAL_ENV_EXAMPLE_PATH)
    )

    assert "*" in ignored
    assert "!.gitignore" in ignored
    for weak_value in ("doris123", "changeme", "admin123", "password123"):
        assert weak_value not in tracked_text
    assert "DORIS_PASSWORD=" not in tracked_text


def test_local_compose_defaults_to_a_dedicated_doris_account() -> None:
    environment = _compose()["services"]["doris-mcp-server"]["environment"]

    assert "DORIS_USER=${DORIS_USER:-mcp_reader}" in environment
    assert "DORIS_USER=root" not in environment


def test_optional_doris_nodes_stay_behind_a_profile_and_pinned_digests() -> None:
    services = _compose()["services"]

    for name, image in PINNED_IMAGES.items():
        assert services[name]["profiles"] == ["doris"]
        assert services[name]["image"] == image
    assert "profiles" not in services["doris-mcp-server"]
    assert all(
        port["host_ip"] == "127.0.0.1" for port in services["doris-fe"]["ports"]
    )
    assert "ports" not in services["doris-be"]
