"""Exercise the complete example Nginx config using isolated synthetic fixtures.

Run directly with Python and a working Docker daemon. No host ports are exposed;
only uniquely named test resources are removed. No real credentials are read.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

SOURCE = (
    Path(__file__).resolve().parents[2] / "example_project/outer_rim_logistics/docker"
)


def run(args: list[str], *, check: bool = True) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            args, check=check, text=True, capture_output=True, timeout=180
        )
    except subprocess.CalledProcessError as error:
        print(error.stdout, file=sys.stderr)
        print(error.stderr, file=sys.stderr)
        raise
    return result.stdout


class MonitoringRuntimeTests(unittest.TestCase):
    def test_https_authentication_and_upstream_header_stripping(self) -> None:
        docker = shutil.which("docker")
        if docker is None:
            self.fail("Docker is required for this explicitly invoked runtime test")
        openssl = shutil.which("openssl")
        if openssl is None:
            self.fail("OpenSSL is required for synthetic TLS/password fixtures")
        assert docker is not None and openssl is not None
        name = f"gm-monitoring-test-{uuid.uuid4().hex}"
        image, network = f"{name}:test", f"{name}-net"
        with tempfile.TemporaryDirectory(prefix="gm-monitoring-test-") as directory:
            fixture = Path(directory)
            fixture.chmod(0o755)
            build = fixture / "build"
            build.mkdir()
            shutil.copyfile(SOURCE / "nginx.Dockerfile", build / "Dockerfile")
            # Only the Dockerfile enters the build context, never the fixtures.
            try:
                run([docker, "build", "-t", image, str(build)])
                shutil.copyfile(SOURCE / "nginx.conf", fixture / "nginx.conf")
                run(
                    [
                        openssl,
                        "req",
                        "-x509",
                        "-nodes",
                        "-newkey",
                        "rsa:2048",
                        "-days",
                        "1",
                        "-subj",
                        "/CN=localhost",
                        "-keyout",
                        str(fixture / "tls.key"),
                        "-out",
                        str(fixture / "tls.crt"),
                    ]
                )
                password_hash = run(
                    [
                        openssl,
                        "passwd",
                        "-apr1",
                        "synthetic-test-only",
                    ]
                ).strip()
                password_file = fixture / "observability"
                password_file.write_text(f"fixture:{password_hash}\n")
                password_file.chmod(0o644)
                (fixture / "backend.conf").write_text(
                    "events {}\nhttp { server { listen 9090; listen 3100; "
                    'location / { return 200 "auth=<$http_authorization>"; } } }\n'
                    "pid /tmp/backend.pid;\nerror_log /tmp/backend-error.log;\n"
                )
                run([docker, "network", "create", "--internal", network])
                command = [
                    docker,
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    network,
                    "--network-alias",
                    "web",
                    "--network-alias",
                    "prometheus",
                    "--network-alias",
                    "loki",
                    "--security-opt",
                    "no-new-privileges:true",
                ]
                mounts = {
                    "nginx.conf": "/etc/nginx/nginx.conf",
                    "tls.key": "/etc/nginx/certs/tls.key",
                    "tls.crt": "/etc/nginx/certs/tls.crt",
                    "observability": "/etc/nginx/htpasswd/observability",
                    "backend.conf": "/etc/nginx/backend.conf",
                }
                for filename, target in mounts.items():
                    command.extend(
                        [
                            "--mount",
                            f"type=bind,src={fixture / filename},dst={target},readonly",
                        ]
                    )
                command.extend(
                    [
                        "--entrypoint",
                        "/bin/sh",
                        image,
                        "-ec",
                        "mkdir -p /var/log/outer-rim/nginx; nginx -t; "
                        'nginx -c /etc/nginx/backend.conf; exec nginx -g "daemon off;"',
                    ]
                )
                run(command)
                run([docker, "exec", name, "nginx", "-t"])
                for host in ("localhost", "_"):
                    for route in ("/prometheus/", "/loki/"):
                        for mode, credentials, expected in (
                            ("absent", None, 401),
                            ("wrong", "fixture:wrong", 401),
                            ("valid", "fixture:synthetic-test-only", 200),
                        ):
                            with self.subTest(host=host, route=route, credentials=mode):
                                request = [
                                    docker,
                                    "exec",
                                    name,
                                    "curl",
                                    "--silent",
                                    "--show-error",
                                    "--insecure",
                                    "--retry",
                                    "10",
                                    "--retry-connrefused",
                                    "--retry-delay",
                                    "1",
                                    "--max-time",
                                    "10",
                                    "-H",
                                    f"Host: {host}",
                                    "-w",
                                    "\n%{http_code}",
                                    f"https://127.0.0.1{route}",
                                ]
                                if credentials is not None:
                                    request.extend(["--user", credentials])
                                body, status = run(request).rsplit("\n", 1)
                                self.assertEqual(int(status), expected)
                                if expected == 200:
                                    self.assertEqual(body, "auth=<>")
            finally:
                # Remove only this invocation's unique resources, even on failure.
                for cleanup in (
                    [docker, "rm", "-f", name],
                    [docker, "network", "rm", network],
                    [docker, "image", "rm", image],
                ):
                    try:
                        run(cleanup, check=False)
                    except (OSError, subprocess.TimeoutExpired) as error:
                        print(f"Test resource cleanup failed: {error}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
