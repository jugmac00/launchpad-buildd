# Copyright 2017-2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import base64
import json
import os.path
import stat
import subprocess
from textwrap import dedent

import responses
from fixtures import FakeLogger, TempDir
from systemfixtures import FakeFilesystem
from testtools import TestCase
from testtools.matchers import AnyMatch, MatchesAll, MatchesListwise, Not

from lpbuildd.target.build_snap import (
    RETCODE_FAILURE_BUILD,
    RETCODE_FAILURE_INSTALL,
)
from lpbuildd.target.cli import parse_args
from lpbuildd.target.tests.matchers import (
    RanAptGet,
    RanBuildCommand,
    RanCommand,
    RanSnap,
)
from lpbuildd.tests.fakebuilder import FakeMethod


class FakeRevisionID(FakeMethod):
    def __init__(self, revision_id):
        super().__init__()
        self.revision_id = revision_id

    def __call__(self, run_args, *args, **kwargs):
        super().__call__(run_args, *args, **kwargs)
        if run_args[:2] == ["bzr", "revno"] or (
            run_args[0] == "git" and "rev-parse" in run_args
        ):
            return "%s\n" % self.revision_id


class FakeSnapcraft(FakeMethod):
    def __init__(self, backend, name):
        super().__init__()
        self.backend = backend
        self.name = name

    def __call__(self, run_args, *args, **kwargs):
        super().__call__(run_args, *args, **kwargs)
        if run_args[0] == "snapcraft" and "cwd" in kwargs:
            self.backend.add_file(os.path.join(kwargs["cwd"], self.name), b"")


class TestBuildSnap(TestCase):
    def test_install_bzr(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet("install", "bzr", "snapcraft"),
                ]
            ),
        )

    def test_install_git(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet("install", "git", "snapcraft"),
                ]
            ),
        )

    @responses.activate
    def test_install_snap_store_proxy(self):
        store_assertion = dedent(
            """\
            type: store
            store: store-id
            url: http://snap-store-proxy.example

            body
            """
        )

        def respond(request):
            return 200, {"X-Assertion-Store-Id": "store-id"}, store_assertion

        responses.add_callback(
            "GET",
            "http://snap-store-proxy.example/v2/auth/store/assertions",
            callback=respond,
        )
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--snap-store-proxy-url",
            "http://snap-store-proxy.example/",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet("install", "git", "snapcraft"),
                    RanCommand(
                        ["snap", "ack", "/dev/stdin"],
                        input_text=store_assertion,
                    ),
                    RanCommand(
                        ["snap", "set", "core", "proxy.store=store-id"]
                    ),
                ]
            ),
        )

    def test_install_proxy(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/lpbuildd-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_snap.install()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet(
                        "install", "python3", "socat", "git", "snapcraft"
                    ),
                    RanCommand(["mkdir", "-p", "/root/.subversion"]),
                ]
            ),
        )
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_snap.backend.backend_fs["/usr/local/bin/lpbuildd-git-proxy"],
        )
        self.assertEqual(
            (
                b"[global]\n"
                b"http-proxy-host = proxy.example\n"
                b"http-proxy-port = 3128\n",
                stat.S_IFREG | 0o644,
            ),
            build_snap.backend.backend_fs["/root/.subversion/servers"],
        )

    def test_install_certificate(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-snap",
            "--use-fetch-service",
            "--fetch-service-mitm-certificate",
            # Base64 content_of_cert
            "Y29udGVudF9vZl9jZXJ0",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/lpbuildd-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_snap.install()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet(
                        "install", "python3", "socat", "git", "snapcraft"
                    ),
                    RanCommand(["mkdir", "-p", "/root/.subversion"]),
                    RanCommand(["rm", "-rf", "/var/lib/apt/lists"]),
                    RanCommand(["update-ca-certificates"]),
                    RanCommand(
                        [
                            "snap",
                            "set",
                            "system",
                            "proxy.http=http://proxy.example:3128/",
                        ]
                    ),
                    RanCommand(
                        [
                            "snap",
                            "set",
                            "system",
                            "proxy.https=http://proxy.example:3128/",
                        ]
                    ),
                    RanAptGet("update"),
                    RanCommand(
                        [
                            "systemctl",
                            "restart",
                            "snapd",
                        ]
                    ),
                ]
            ),
        )
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_snap.backend.backend_fs["/usr/local/bin/lpbuildd-git-proxy"],
        )
        self.assertEqual(
            (
                b"[global]\n"
                b"http-proxy-host = proxy.example\n"
                b"http-proxy-port = 3128\n",
                stat.S_IFREG | 0o644,
            ),
            build_snap.backend.backend_fs["/root/.subversion/servers"],
        )
        self.assertEqual(
            (
                b"content_of_cert",
                stat.S_IFREG | 0o644,
            ),
            build_snap.backend.backend_fs[
                "/usr/local/share/ca-certificates/local-ca.crt"
            ],
        )
        self.assertEqual(
            (
                dedent(
                    """\
                Acquire::http::Proxy "http://proxy.example:3128/";
                Acquire::https::Proxy "http://proxy.example:3128/";

                """
                ).encode("UTF-8"),
                stat.S_IFREG | 0o644,
            ),
            build_snap.backend.backend_fs["/etc/apt/apt.conf.d/99proxy"],
        )

    def test_install_snapd_proxy(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-snap",
            "--use-fetch-service",
            "--fetch-service-mitm-certificate",
            # Base64 content_of_cert
            "Y29udGVudF9vZl9jZXJ0",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/lpbuildd-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_snap.install()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet(
                        "install", "python3", "socat", "git", "snapcraft"
                    ),
                    RanCommand(["mkdir", "-p", "/root/.subversion"]),
                    RanCommand(["rm", "-rf", "/var/lib/apt/lists"]),
                    RanCommand(["update-ca-certificates"]),
                    RanCommand(
                        [
                            "snap",
                            "set",
                            "system",
                            "proxy.http=http://proxy.example:3128/",
                        ]
                    ),
                    RanCommand(
                        [
                            "snap",
                            "set",
                            "system",
                            "proxy.https=http://proxy.example:3128/",
                        ]
                    ),
                    RanAptGet("update"),
                    RanCommand(
                        [
                            "systemctl",
                            "restart",
                            "snapd",
                        ]
                    ),
                ]
            ),
        )
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_snap.backend.backend_fs["/usr/local/bin/lpbuildd-git-proxy"],
        )
        self.assertEqual(
            (
                b"[global]\n"
                b"http-proxy-host = proxy.example\n"
                b"http-proxy-port = 3128\n",
                stat.S_IFREG | 0o644,
            ),
            build_snap.backend.backend_fs["/root/.subversion/servers"],
        )
        self.assertEqual(
            (
                dedent(
                    """\
                Acquire::http::Proxy "http://proxy.example:3128/";
                Acquire::https::Proxy "http://proxy.example:3128/";

                """
                ).encode("UTF-8"),
                stat.S_IFREG | 0o644,
            ),
            build_snap.backend.backend_fs["/etc/apt/apt.conf.d/99proxy"],
        )

    def test_install_channels(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--channel=core=candidate",
            "--channel=core18=beta",
            "--channel=snapcraft=edge",
            "--channel=snapd=edge",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet("install", "bzr", "sudo"),
                    RanSnap("install", "--channel=candidate", "core"),
                    RanSnap("refresh", "--channel=candidate", "core"),
                    RanSnap("install", "--channel=beta", "core18"),
                    RanSnap("refresh", "--channel=beta", "core18"),
                    RanSnap("install", "--channel=edge", "snapd"),
                    RanSnap("refresh", "--channel=edge", "snapd"),
                    RanSnap(
                        "install", "--classic", "--channel=edge", "snapcraft"
                    ),
                ]
            ),
        )

    def test_install_fetch_service(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-snap",
            "--use-fetch-service",
            "--fetch-service-mitm-certificate",
            # Base64 content_of_cert
            "Y29udGVudF9vZl9jZXJ0",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesAll(
                Not(
                    AnyMatch(
                        RanCommand(
                            [
                                "git",
                                "config",
                                "--global",
                                "protocol.version",
                                "2",
                            ]
                        )
                    )
                ),
            ),
        )

    def test_install_fetch_service_focal(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=focal",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-snap",
            "--use-fetch-service",
            "--fetch-service-mitm-certificate",
            # Base64 content_of_cert
            "Y29udGVudF9vZl9jZXJ0",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesAll(
                AnyMatch(
                    RanCommand(
                        ["git", "config", "--global", "protocol.version", "2"]
                    )
                ),
            ),
        )

    def test_repo_bzr(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("42")
        build_snap.repo()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["bzr", "branch", "lp:foo", "test-snap"], cwd="/build"
                    ),
                    RanBuildCommand(
                        ["bzr", "revno"],
                        cwd="/build/test-snap",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_snap.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "42"}, json.load(status))

    def test_repo_git(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("0" * 40)
        build_snap.repo()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["git", "clone", "-n", "lp:foo", "test-snap"],
                        cwd="/build",
                    ),
                    RanBuildCommand(
                        ["git", "checkout", "-q", "HEAD"],
                        cwd="/build/test-snap",
                    ),
                    RanBuildCommand(
                        [
                            "git",
                            "submodule",
                            "update",
                            "--init",
                            "--recursive",
                        ],
                        cwd="/build/test-snap",
                    ),
                    RanBuildCommand(
                        ["git", "rev-parse", "HEAD^{}"],
                        cwd="/build/test-snap",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_snap.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_git_with_path(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--git-path",
            "next",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("0" * 40)
        build_snap.repo()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["git", "clone", "-n", "lp:foo", "test-snap"],
                        cwd="/build",
                    ),
                    RanBuildCommand(
                        ["git", "checkout", "-q", "next"],
                        cwd="/build/test-snap",
                    ),
                    RanBuildCommand(
                        [
                            "git",
                            "submodule",
                            "update",
                            "--init",
                            "--recursive",
                        ],
                        cwd="/build/test-snap",
                    ),
                    RanBuildCommand(
                        ["git", "rev-parse", "next^{}"],
                        cwd="/build/test-snap",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_snap.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_git_with_tag_path(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--git-path",
            "refs/tags/1.0",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("0" * 40)
        build_snap.repo()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["git", "clone", "-n", "lp:foo", "test-snap"],
                        cwd="/build",
                    ),
                    RanBuildCommand(
                        ["git", "checkout", "-q", "refs/tags/1.0"],
                        cwd="/build/test-snap",
                    ),
                    RanBuildCommand(
                        [
                            "git",
                            "submodule",
                            "update",
                            "--init",
                            "--recursive",
                        ],
                        cwd="/build/test-snap",
                    ),
                    RanBuildCommand(
                        ["git", "rev-parse", "refs/tags/1.0^{}"],
                        cwd="/build/test-snap",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_snap.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_proxy(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("0" * 40)
        build_snap.repo()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "HTTP_PROXY": "http://proxy.example:3128/",
            "HTTPS_PROXY": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/lpbuildd-git-proxy",
            "SNAPPY_STORE_NO_CDN": "1",
        }
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["git", "clone", "-n", "lp:foo", "test-snap"],
                        cwd="/build",
                        **env,
                    ),
                    RanBuildCommand(
                        ["git", "checkout", "-q", "HEAD"],
                        cwd="/build/test-snap",
                        **env,
                    ),
                    RanBuildCommand(
                        [
                            "git",
                            "submodule",
                            "update",
                            "--init",
                            "--recursive",
                        ],
                        cwd="/build/test-snap",
                        **env,
                    ),
                    RanBuildCommand(
                        ["git", "rev-parse", "HEAD^{}"],
                        cwd="/build/test-snap",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_snap.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_fetch_service(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-snap",
            "--use-fetch-service",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("0" * 40)
        build_snap.repo()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "HTTP_PROXY": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "HTTPS_PROXY": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/lpbuildd-git-proxy",
            "SNAPPY_STORE_NO_CDN": "1",
            "CARGO_HTTP_CAINFO": (
                "/usr/local/share/ca-certificates/local-ca.crt"
            ),
            "REQUESTS_CA_BUNDLE": (
                "/usr/local/share/ca-certificates/local-ca.crt"
            ),
            "GOPROXY": "direct",
        }
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        [
                            "git",
                            "clone",
                            "-n",
                            "--depth",
                            "1",
                            "-b",
                            "HEAD",
                            "--single-branch",
                            "lp:foo",
                            "test-snap",
                        ],
                        cwd="/build",
                        **env,
                    ),
                    RanBuildCommand(
                        ["git", "checkout", "-q", "HEAD"],
                        cwd="/build/test-snap",
                        **env,
                    ),
                    RanBuildCommand(
                        [
                            "git",
                            "submodule",
                            "update",
                            "--init",
                            "--recursive",
                        ],
                        cwd="/build/test-snap",
                        **env,
                    ),
                    RanBuildCommand(
                        ["git", "rev-parse", "HEAD^{}"],
                        cwd="/build/test-snap",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_snap.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_pull(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.pull()
        env = {
            "SNAPCRAFT_LOCAL_SOURCES": "1",
            "SNAPCRAFT_SETUP_CORE": "1",
            "SNAPCRAFT_BUILD_INFO": "1",
            "SNAPCRAFT_IMAGE_INFO": "{}",
            "SNAPCRAFT_BUILD_ENVIRONMENT": "host",
        }
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft", "pull"], cwd="/build/test-snap", **env
                    ),
                ]
            ),
        )

    def test_pull_with_launchpad_instance(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--build-url",
            "https://launchpad.example/build",
            "--branch",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-snap",
            "--launchpad-server-url=launchpad.test",
            "--launchpad-instance=devel",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.pull()
        env = {
            "SNAPCRAFT_LOCAL_SOURCES": "1",
            "SNAPCRAFT_SETUP_CORE": "1",
            "SNAPCRAFT_BUILD_INFO": "1",
            "SNAPCRAFT_IMAGE_INFO": (
                '{"build_url": "https://launchpad.example/build"}'
            ),
            "SNAPCRAFT_BUILD_ENVIRONMENT": "host",
            "http_proxy": "http://proxy.example:3128/",
            "HTTP_PROXY": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "HTTPS_PROXY": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/lpbuildd-git-proxy",
            "SNAPPY_STORE_NO_CDN": "1",
            "LAUNCHPAD_INSTANCE": "devel",
            "LAUNCHPAD_SERVER_URL": "launchpad.test",
        }
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft", "pull"], cwd="/build/test-snap", **env
                    ),
                ]
            ),
        )

    def test_pull_proxy(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--build-url",
            "https://launchpad.example/build",
            "--branch",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.pull()
        env = {
            "SNAPCRAFT_LOCAL_SOURCES": "1",
            "SNAPCRAFT_SETUP_CORE": "1",
            "SNAPCRAFT_BUILD_INFO": "1",
            "SNAPCRAFT_IMAGE_INFO": (
                '{"build_url": "https://launchpad.example/build"}'
            ),
            "SNAPCRAFT_BUILD_ENVIRONMENT": "host",
            "http_proxy": "http://proxy.example:3128/",
            "HTTP_PROXY": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "HTTPS_PROXY": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/lpbuildd-git-proxy",
            "SNAPPY_STORE_NO_CDN": "1",
        }
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft", "pull"], cwd="/build/test-snap", **env
                    ),
                ]
            ),
        )

    @responses.activate
    def test_pull_disable_proxy_after_pull(self):
        self.useFixture(FakeLogger())
        responses.add("DELETE", "http://proxy-auth.example/tokens/1")
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--build-url",
            "https://launchpad.example/build",
            "--branch",
            "lp:foo",
            "--proxy-url",
            "http://localhost:8222/",
            "--upstream-proxy-url",
            "http://username:password@proxy.example:3128/",
            "--revocation-endpoint",
            "http://proxy-auth.example/tokens/1",
            "--disable-proxy-after-pull",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.pull()
        env = {
            "SNAPCRAFT_LOCAL_SOURCES": "1",
            "SNAPCRAFT_SETUP_CORE": "1",
            "SNAPCRAFT_BUILD_INFO": "1",
            "SNAPCRAFT_IMAGE_INFO": (
                '{"build_url": "https://launchpad.example/build"}'
            ),
            "SNAPCRAFT_BUILD_ENVIRONMENT": "host",
            "http_proxy": "http://localhost:8222/",
            "HTTP_PROXY": "http://localhost:8222/",
            "https_proxy": "http://localhost:8222/",
            "HTTPS_PROXY": "http://localhost:8222/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/lpbuildd-git-proxy",
            "SNAPPY_STORE_NO_CDN": "1",
        }
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft", "pull"], cwd="/build/test-snap", **env
                    ),
                ]
            ),
        )
        self.assertEqual(1, len(responses.calls))
        request = responses.calls[0].request
        auth = base64.b64encode(b"username:password").decode()
        self.assertEqual(f"Basic {auth}", request.headers["Authorization"])
        self.assertEqual("http://proxy-auth.example/tokens/1", request.url)
        # XXX cjwatson 2023-02-07: Ideally we'd check the timeout as well,
        # but the version of responses in Ubuntu 20.04 doesn't store it
        # anywhere we can get at it.

    def test_pull_build_source_tarball(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--build-source-tarball",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.pull()
        env = {
            "SNAPCRAFT_LOCAL_SOURCES": "1",
            "SNAPCRAFT_SETUP_CORE": "1",
            "SNAPCRAFT_BUILD_INFO": "1",
            "SNAPCRAFT_IMAGE_INFO": "{}",
            "SNAPCRAFT_BUILD_ENVIRONMENT": "host",
        }
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft", "pull"], cwd="/build/test-snap", **env
                    ),
                    RanBuildCommand(
                        [
                            "tar",
                            "-czf",
                            "test-snap.tar.gz",
                            "--format=gnu",
                            "--sort=name",
                            "--exclude-vcs",
                            "--numeric-owner",
                            "--owner=0",
                            "--group=0",
                            "test-snap",
                        ],
                        cwd="/build",
                    ),
                ]
            ),
        )

    def test_pull_private(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--private",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.pull()
        env = {
            "SNAPCRAFT_LOCAL_SOURCES": "1",
            "SNAPCRAFT_SETUP_CORE": "1",
            "SNAPCRAFT_IMAGE_INFO": "{}",
            "SNAPCRAFT_BUILD_ENVIRONMENT": "host",
        }
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft", "pull"], cwd="/build/test-snap", **env
                    ),
                ]
            ),
        )

    def test_build(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.run = FakeSnapcraft(
            build_snap.backend, "test-snap_1.snap"
        )
        build_snap.build()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft"],
                        cwd="/build/test-snap",
                        SNAPCRAFT_BUILD_INFO="1",
                        SNAPCRAFT_IMAGE_INFO="{}",
                        SNAPCRAFT_BUILD_ENVIRONMENT="host",
                    ),
                    RanBuildCommand(
                        ["sha512sum", "test-snap_1.snap"],
                        cwd="/build/test-snap",
                    ),
                ]
            ),
        )

    def test_build_with_launchpad_instance(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-snap",
            "--launchpad-server-url=launchpad.test",
            "--launchpad-instance=devel",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.run = FakeSnapcraft(
            build_snap.backend, "test-snap_1.snap"
        )
        build_snap.build()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft"],
                        cwd="/build/test-snap",
                        SNAPCRAFT_BUILD_INFO="1",
                        SNAPCRAFT_IMAGE_INFO="{}",
                        SNAPCRAFT_BUILD_ENVIRONMENT="host",
                        LAUNCHPAD_INSTANCE="devel",
                        LAUNCHPAD_SERVER_URL="launchpad.test",
                    ),
                    RanBuildCommand(
                        ["sha512sum", "test-snap_1.snap"],
                        cwd="/build/test-snap",
                    ),
                ]
            ),
        )

    def test_build_proxy(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--build-url",
            "https://launchpad.example/build",
            "--branch",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.run = FakeSnapcraft(
            build_snap.backend, "test-snap_1.snap"
        )
        build_snap.build()
        env = {
            "SNAPCRAFT_BUILD_INFO": "1",
            "SNAPCRAFT_IMAGE_INFO": (
                '{"build_url": "https://launchpad.example/build"}'
            ),
            "SNAPCRAFT_BUILD_ENVIRONMENT": "host",
            "http_proxy": "http://proxy.example:3128/",
            "HTTP_PROXY": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "HTTPS_PROXY": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/lpbuildd-git-proxy",
            "SNAPPY_STORE_NO_CDN": "1",
        }
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft"], cwd="/build/test-snap", **env
                    ),
                    RanBuildCommand(
                        ["sha512sum", "test-snap_1.snap"],
                        cwd="/build/test-snap",
                    ),
                ]
            ),
        )

    def test_build_private(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--private",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.run = FakeSnapcraft(
            build_snap.backend, "test-snap_1.snap"
        )
        build_snap.build()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft"],
                        cwd="/build/test-snap",
                        SNAPCRAFT_IMAGE_INFO="{}",
                        SNAPCRAFT_BUILD_ENVIRONMENT="host",
                    ),
                    RanBuildCommand(
                        ["sha512sum", "test-snap_1.snap"],
                        cwd="/build/test-snap",
                    ),
                ]
            ),
        )

    def test_build_including_build_request_id(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--build-request-id",
            "13",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.run = FakeSnapcraft(
            build_snap.backend, "test-snap_1.snap"
        )
        build_snap.build()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft"],
                        cwd="/build/test-snap",
                        SNAPCRAFT_BUILD_INFO="1",
                        SNAPCRAFT_IMAGE_INFO='{"build-request-id": "lp-13"}',
                        SNAPCRAFT_BUILD_ENVIRONMENT="host",
                    ),
                    RanBuildCommand(
                        ["sha512sum", "test-snap_1.snap"],
                        cwd="/build/test-snap",
                    ),
                ]
            ),
        )

    def test_build_including_build_request_timestamp(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--build-request-timestamp",
            "2018-04-13T14:50:02Z",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.run = FakeSnapcraft(
            build_snap.backend, "test-snap_1.snap"
        )
        build_snap.build()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft"],
                        cwd="/build/test-snap",
                        SNAPCRAFT_BUILD_INFO="1",
                        SNAPCRAFT_IMAGE_INFO=(
                            '{"build-request-timestamp": '
                            '"2018-04-13T14:50:02Z"}'
                        ),
                        SNAPCRAFT_BUILD_ENVIRONMENT="host",
                    ),
                    RanBuildCommand(
                        ["sha512sum", "test-snap_1.snap"],
                        cwd="/build/test-snap",
                    ),
                ]
            ),
        )

    def test_build_target_architectures(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--target-arch",
            "i386",
            "--target-arch",
            "amd64",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.build()
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["snapcraft"],
                        cwd="/build/test-snap",
                        SNAPCRAFT_BUILD_INFO="1",
                        SNAPCRAFT_IMAGE_INFO="{}",
                        SNAPCRAFT_BUILD_ENVIRONMENT="host",
                        SNAPCRAFT_BUILD_FOR="i386",
                    ),
                ]
            ),
        )

    # XXX cjwatson 2017-08-07: Test revoke_token.  It may be easiest to
    # convert it to requests first.

    def test_run_succeeds(self):
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--build-request-id",
            "13",
            "--build-url",
            "https://launchpad.example/build",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("42")
        self.assertEqual(0, build_snap.run())
        self.assertThat(
            build_snap.backend.run.calls,
            MatchesAll(
                AnyMatch(RanAptGet("install", "bzr", "snapcraft")),
                AnyMatch(
                    RanBuildCommand(
                        ["bzr", "branch", "lp:foo", "test-snap"], cwd="/build"
                    )
                ),
                AnyMatch(
                    RanBuildCommand(
                        ["snapcraft", "pull"],
                        cwd="/build/test-snap",
                        SNAPCRAFT_LOCAL_SOURCES="1",
                        SNAPCRAFT_SETUP_CORE="1",
                        SNAPCRAFT_BUILD_INFO="1",
                        SNAPCRAFT_IMAGE_INFO=(
                            '{"build-request-id": "lp-13",'
                            ' "build_url": "https://launchpad.example/build"}'
                        ),
                        SNAPCRAFT_BUILD_ENVIRONMENT="host",
                    )
                ),
                AnyMatch(
                    RanBuildCommand(
                        ["snapcraft"],
                        cwd="/build/test-snap",
                        SNAPCRAFT_BUILD_INFO="1",
                        SNAPCRAFT_IMAGE_INFO=(
                            '{"build-request-id": "lp-13",'
                            ' "build_url": "https://launchpad.example/build"}'
                        ),
                        SNAPCRAFT_BUILD_ENVIRONMENT="host",
                    )
                ),
            ),
        )

    def test_run_install_fails(self):
        class FailInstall(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super().__call__(run_args, *args, **kwargs)
                if run_args[0] == "apt-get":
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.run = FailInstall()
        self.assertEqual(RETCODE_FAILURE_INSTALL, build_snap.run())

    def test_run_repo_fails(self):
        class FailRepo(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super().__call__(run_args, *args, **kwargs)
                if run_args[:2] == ["bzr", "branch"]:
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.run = FailRepo()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_snap.run())

    def test_run_pull_fails(self):
        class FailPull(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super().__call__(run_args, *args, **kwargs)
                if run_args[:2] == ["bzr", "revno"]:
                    return "42\n"
                elif run_args[:2] == ["snapcraft", "pull"]:
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FailPull()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_snap.run())

    def test_run_build_fails(self):
        class FailBuild(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super().__call__(run_args, *args, **kwargs)
                if run_args[:2] == ["bzr", "revno"]:
                    return "42\n"
                elif run_args == ["snapcraft"]:
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "buildsnap",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-snap",
        ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FailBuild()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_snap.run())
