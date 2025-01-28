import json
import os
import stat
import subprocess
from textwrap import dedent

import responses
from fixtures import FakeLogger, TempDir
from systemfixtures import FakeFilesystem
from testtools.matchers import AnyMatch, MatchesAll, MatchesListwise, Not
from testtools.testcase import TestCase

from lpbuildd.target.backend import InvalidBuildFilePath
from lpbuildd.target.build_craft import (
    RETCODE_FAILURE_BUILD,
    RETCODE_FAILURE_INSTALL,
)
from lpbuildd.target.cli import parse_args
from lpbuildd.target.tests.matchers import (
    RanAptGet,
    RanBuildCommand,
    RanCommand,
)
from lpbuildd.target.tests.test_build_snap import FakeRevisionID, RanSnap
from lpbuildd.tests.fakebuilder import FakeMethod


class TestBuildCraft(TestCase):
    def test_run_build_command_no_env(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.run_build_command(["echo", "hello world"])
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["echo", "hello world"], cwd="/home/buildd/test-image"
                    ),
                ]
            ),
        )

    def test_run_build_command_env(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.run_build_command(
            ["echo", "hello world"], env={"FOO": "bar baz"}
        )
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["echo", "hello world"],
                        FOO="bar baz",
                        cwd="/home/buildd/test-image",
                    )
                ]
            ),
        )

    def test_install_channels(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--channel=core=candidate",
            "--channel=core18=beta",
            "--channel=sourcecraft=edge",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.install()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet("install", "bzr"),
                    RanSnap("install", "--channel=candidate", "core"),
                    RanSnap("install", "--channel=beta", "core18"),
                    RanSnap(
                        "install", "--classic", "--channel=edge", "sourcecraft"
                    ),
                    RanCommand(["mkdir", "-p", "/home/buildd"]),
                ]
            ),
        )

    def test_install_bzr(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.install()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet("install", "bzr"),
                    RanSnap(
                        "install",
                        "--classic",
                        "--channel=latest/edge/craftctl",
                        "sourcecraft",
                    ),
                    RanCommand(["mkdir", "-p", "/home/buildd"]),
                ]
            ),
        )

    def test_install_git(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.install()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet("install", "git"),
                    RanSnap(
                        "install",
                        "--classic",
                        "--channel=latest/edge/craftctl",
                        "sourcecraft",
                    ),
                    RanCommand(["mkdir", "-p", "/home/buildd"]),
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
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--snap-store-proxy-url",
            "http://snap-store-proxy.example/",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.install()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet("install", "git"),
                    RanCommand(
                        ["snap", "ack", "/dev/stdin"],
                        input_text=store_assertion,
                    ),
                    RanCommand(
                        ["snap", "set", "core", "proxy.store=store-id"]
                    ),
                    RanSnap(
                        "install",
                        "--classic",
                        "--channel=latest/edge/craftctl",
                        "sourcecraft",
                    ),
                    RanCommand(["mkdir", "-p", "/home/buildd"]),
                ]
            ),
        )

    def test_install_proxy(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/lpbuildd-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_craft.install()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet(
                        "install",
                        "python3",
                        "socat",
                        "git",
                    ),
                    RanSnap(
                        "install",
                        "--classic",
                        "--channel=latest/edge/craftctl",
                        "sourcecraft",
                    ),
                    RanCommand(["mkdir", "-p", "/home/buildd"]),
                ]
            ),
        )
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_craft.backend.backend_fs[
                "/usr/local/bin/lpbuildd-git-proxy"
            ],
        )

    def test_install_certificate(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-image",
            "--use-fetch-service",
            "--fetch-service-mitm-certificate",
            # Base64 content_of_cert
            "Y29udGVudF9vZl9jZXJ0",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/lpbuildd-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_craft.install()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet(
                        "install",
                        "python3",
                        "socat",
                        "git",
                    ),
                    RanSnap(
                        "install",
                        "--classic",
                        "--channel=latest/edge/craftctl",
                        "sourcecraft",
                    ),
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
                    RanCommand(["mkdir", "-p", "/home/buildd"]),
                ]
            ),
        )
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_craft.backend.backend_fs[
                "/usr/local/bin/lpbuildd-git-proxy"
            ],
        )
        self.assertEqual(
            (
                b"content_of_cert",
                stat.S_IFREG | 0o644,
            ),
            build_craft.backend.backend_fs[
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
            build_craft.backend.backend_fs["/etc/apt/apt.conf.d/99proxy"],
        )

    def test_install_snapd_proxy(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-image",
            "--use-fetch-service",
            "--fetch-service-mitm-certificate",
            # Base64 content_of_cert
            "Y29udGVudF9vZl9jZXJ0",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/lpbuildd-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_craft.install()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet(
                        "install",
                        "python3",
                        "socat",
                        "git",
                    ),
                    RanSnap(
                        "install",
                        "--classic",
                        "--channel=latest/edge/craftctl",
                        "sourcecraft",
                    ),
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
                    RanCommand(["mkdir", "-p", "/home/buildd"]),
                ]
            ),
        )
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_craft.backend.backend_fs[
                "/usr/local/bin/lpbuildd-git-proxy"
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
            build_craft.backend.backend_fs["/etc/apt/apt.conf.d/99proxy"],
        )

    def test_install_fetch_service(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-image",
            "--use-fetch-service",
            "--fetch-service-mitm-certificate",
            # Base64 content_of_cert
            "Y29udGVudF9vZl9jZXJ0",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/lpbuildd-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_craft.install()
        self.assertThat(
            build_craft.backend.run.calls,
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
            "build-craft",
            "--backend=fake",
            "--series=focal",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-image",
            "--use-fetch-service",
            "--fetch-service-mitm-certificate",
            # Base64 content_of_cert
            "Y29udGVudF9vZl9jZXJ0",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/lpbuildd-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_craft.install()
        self.assertThat(
            build_craft.backend.run.calls,
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
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.build_path = self.useFixture(TempDir()).path
        build_craft.backend.run = FakeRevisionID("42")
        build_craft.repo()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["bzr", "branch", "lp:foo", "test-image"],
                        cwd="/home/buildd",
                    ),
                    RanBuildCommand(
                        ["bzr", "revno"],
                        cwd="/home/buildd/test-image",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_craft.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "42"}, json.load(status))

    def test_repo_git(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.build_path = self.useFixture(TempDir()).path
        build_craft.backend.run = FakeRevisionID("0" * 40)
        build_craft.repo()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["git", "clone", "-n", "lp:foo", "test-image"],
                        cwd="/home/buildd",
                    ),
                    RanBuildCommand(
                        ["git", "checkout", "-q", "HEAD"],
                        cwd="/home/buildd/test-image",
                    ),
                    RanBuildCommand(
                        [
                            "git",
                            "submodule",
                            "update",
                            "--init",
                            "--recursive",
                        ],
                        cwd="/home/buildd/test-image",
                    ),
                    RanBuildCommand(
                        ["git", "rev-parse", "HEAD^{}"],
                        cwd="/home/buildd/test-image",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_craft.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_git_with_path(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--git-path",
            "next",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.build_path = self.useFixture(TempDir()).path
        build_craft.backend.run = FakeRevisionID("0" * 40)
        build_craft.repo()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["git", "clone", "-n", "lp:foo", "test-image"],
                        cwd="/home/buildd",
                    ),
                    RanBuildCommand(
                        ["git", "checkout", "-q", "next"],
                        cwd="/home/buildd/test-image",
                    ),
                    RanBuildCommand(
                        [
                            "git",
                            "submodule",
                            "update",
                            "--init",
                            "--recursive",
                        ],
                        cwd="/home/buildd/test-image",
                    ),
                    RanBuildCommand(
                        ["git", "rev-parse", "next^{}"],
                        cwd="/home/buildd/test-image",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_craft.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_git_with_tag_path(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--git-path",
            "refs/tags/1.0",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.build_path = self.useFixture(TempDir()).path
        build_craft.backend.run = FakeRevisionID("0" * 40)
        build_craft.repo()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["git", "clone", "-n", "lp:foo", "test-image"],
                        cwd="/home/buildd",
                    ),
                    RanBuildCommand(
                        ["git", "checkout", "-q", "refs/tags/1.0"],
                        cwd="/home/buildd/test-image",
                    ),
                    RanBuildCommand(
                        [
                            "git",
                            "submodule",
                            "update",
                            "--init",
                            "--recursive",
                        ],
                        cwd="/home/buildd/test-image",
                    ),
                    RanBuildCommand(
                        ["git", "rev-parse", "refs/tags/1.0^{}"],
                        cwd="/home/buildd/test-image",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_craft.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_proxy(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.build_path = self.useFixture(TempDir()).path
        build_craft.backend.run = FakeRevisionID("0" * 40)
        build_craft.repo()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/lpbuildd-git-proxy",
            "SNAPPY_STORE_NO_CDN": "1",
        }
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["git", "clone", "-n", "lp:foo", "test-image"],
                        cwd="/home/buildd",
                        **env,
                    ),
                    RanBuildCommand(
                        ["git", "checkout", "-q", "HEAD"],
                        cwd="/home/buildd/test-image",
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
                        cwd="/home/buildd/test-image",
                        **env,
                    ),
                    RanBuildCommand(
                        ["git", "rev-parse", "HEAD^{}"],
                        cwd="/home/buildd/test-image",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_craft.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_fetch_service(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-image",
            "--use-fetch-service",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.build_path = self.useFixture(TempDir()).path
        build_craft.backend.run = FakeRevisionID("0" * 40)
        build_craft.repo()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
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
            build_craft.backend.run.calls,
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
                            "test-image",
                        ],
                        cwd="/home/buildd",
                        **env,
                    ),
                    RanBuildCommand(
                        ["git", "checkout", "-q", "HEAD"],
                        cwd="/home/buildd/test-image",
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
                        cwd="/home/buildd/test-image",
                        **env,
                    ),
                    RanBuildCommand(
                        ["git", "rev-parse", "HEAD^{}"],
                        cwd="/home/buildd/test-image",
                        get_output=True,
                        universal_newlines=True,
                    ),
                ]
            ),
        )
        status_path = os.path.join(build_craft.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_build(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.add_dir("/build/test-directory")
        build_craft.build()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["sourcecraft", "pack", "-v", "--destructive-mode"],
                        cwd="/home/buildd/test-image/.",
                    ),
                ]
            ),
        )

    def test_build_with_launchpad_instance(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
            "--launchpad-server-url=launchpad.test",
            "--launchpad-instance=devel",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.add_dir("/build/test-directory")
        build_craft.build()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["sourcecraft", "pack", "-v", "--destructive-mode"],
                        cwd="/home/buildd/test-image/.",
                        LAUNCHPAD_INSTANCE="devel",
                        LAUNCHPAD_SERVER_URL="launchpad.test",
                    ),
                ]
            ),
        )

    def test_build_with_path(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--build-path",
            "build-aux/",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.add_dir("/build/test-directory")
        build_craft.build()
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["sourcecraft", "pack", "-v", "--destructive-mode"],
                        cwd="/home/buildd/test-image/build-aux/",
                    ),
                ]
            ),
        )

    def test_build_proxy(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.build()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/lpbuildd-git-proxy",
            "SNAPPY_STORE_NO_CDN": "1",
        }
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["sourcecraft", "pack", "-v", "--destructive-mode"],
                        cwd="/home/buildd/test-image/.",
                        **env,
                    ),
                ]
            ),
        )

    def test_build_fetch_service(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--proxy-url",
            "http://proxy.example:3128/",
            "test-image",
            "--use-fetch-service",
            "--fetch-service-mitm-certificate",
            # Base64 content_of_cert
            "Y29udGVudF9vZl9jZXJ0",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.build()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
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
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["sourcecraft", "pack", "-v", "--destructive-mode"],
                        cwd="/home/buildd/test-image/.",
                        **env,
                    ),
                ]
            ),
        )

    def test_run_succeeds(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.build_path = self.useFixture(TempDir()).path
        build_craft.backend.run = FakeRevisionID("42")
        self.assertEqual(0, build_craft.run())
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesAll(
                AnyMatch(
                    RanAptGet("install", "bzr"),
                ),
                AnyMatch(
                    RanBuildCommand(
                        ["bzr", "branch", "lp:foo", "test-image"],
                        cwd="/home/buildd",
                    )
                ),
                AnyMatch(
                    RanBuildCommand(
                        ["sourcecraft", "pack", "-v", "--destructive-mode"],
                        cwd="/home/buildd/test-image/.",
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
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.run = FailInstall()
        self.assertEqual(RETCODE_FAILURE_INSTALL, build_craft.run())

    def test_run_repo_fails(self):
        class FailRepo(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super().__call__(run_args, *args, **kwargs)
                if run_args[:2] == ["bzr", "branch"]:
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.run = FailRepo()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_craft.run())

    def test_run_build_fails(self):
        class FailBuild(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super().__call__(run_args, *args, **kwargs)
                if run_args[0] == "sourcecraft":
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.build_path = self.useFixture(TempDir()).path
        build_craft.backend.run = FailBuild()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_craft.run())

    def test_build_with_invalid_build_path_parent(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--build-path",
            "../",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.add_dir("/build/test-directory")
        self.assertRaises(InvalidBuildFilePath, build_craft.build)

    def test_build_with_invalid_build_path_absolute(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--build-path",
            "/etc",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.backend.add_dir("/build/test-directory")
        self.assertRaises(InvalidBuildFilePath, build_craft.build)

    def test_build_with_invalid_build_path_symlink(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--build-path",
            "build/",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.buildd_path = self.useFixture(TempDir()).path
        os.symlink(
            "/etc/hosts", os.path.join(build_craft.buildd_path, "build")
        )
        self.assertRaises(InvalidBuildFilePath, build_craft.build)

    def test_build_with_cargo_credentials(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--environment-variable",
            "CARGO_REGISTRIES_ARTIFACTORY1_INDEX=sparse+https://canonical.example.com/artifactory/api/cargo/cargo-upstream1/index/",
            "--environment-variable",
            "CARGO_REGISTRIES_ARTIFACTORY1_TOKEN=Bearer token1",
            "--environment-variable",
            "CARGO_REGISTRIES_ARTIFACTORY2_INDEX=sparse+https://canonical.example.com/artifactory/api/cargo/cargo-upstream2/index/",
            "--environment-variable",
            "CARGO_REGISTRIES_ARTIFACTORY2_TOKEN=Bearer token2",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.build()

        # Verify the build command was run with correct environment
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["sourcecraft", "pack", "-v", "--destructive-mode"],
                        cwd="/home/buildd/test-image/.",
                        CARGO_REGISTRIES_ARTIFACTORY1_INDEX="sparse+https://canonical.example.com/artifactory/api/cargo/cargo-upstream1/index/",
                        CARGO_REGISTRIES_ARTIFACTORY1_TOKEN="Bearer token1",
                        CARGO_REGISTRIES_ARTIFACTORY2_INDEX="sparse+https://canonical.example.com/artifactory/api/cargo/cargo-upstream2/index/",
                        CARGO_REGISTRIES_ARTIFACTORY2_TOKEN="Bearer token2",
                    ),
                ]
            ),
        )

    def test_build_with_maven_credentials(self):
        args = [
            "build-craft",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "--environment-variable",
            "MAVEN_ARTIFACTORY3_URL=https://canonical.example.com/artifactory/api/maven/maven-upstream3/",
            "--environment-variable",
            "MAVEN_ARTIFACTORY3_READ_AUTH=user3:token3",
            "--environment-variable",
            "MAVEN_ARTIFACTORY4_URL=https://canonical.example.com/artifactory/api/maven/maven-upstream4/",
            "--environment-variable",
            "MAVEN_ARTIFACTORY4_READ_AUTH=user4:token4",
            "test-image",
        ]
        build_craft = parse_args(args=args).operation
        build_craft.build()

        # Check that .m2/settings.xml was created correctly
        maven_settings_path = "/home/buildd/test-image/.m2/settings.xml"
        self.assertTrue(build_craft.backend.path_exists(maven_settings_path))
        with build_craft.backend.open(maven_settings_path) as f:
            settings_content = f.read()
            self.assertIn('<id>artifactory3</id>', settings_content)
            self.assertIn('<username>user3</username>', settings_content)
            self.assertIn('<password>token3</password>', settings_content)
            self.assertIn('<id>artifactory4</id>', settings_content)
            self.assertIn('<username>user4</username>', settings_content)
            self.assertIn('<password>token4</password>', settings_content)

        # Verify the build command was run
        self.assertThat(
            build_craft.backend.run.calls,
            MatchesListwise(
                [
                    RanCommand(["mkdir", "-p", "/home/buildd/test-image/.m2"]),
                    RanBuildCommand(
                        ["sourcecraft", "pack", "-v", "--destructive-mode"],
                        cwd="/home/buildd/test-image/.",
                    ),
                ]
            ),
        )
