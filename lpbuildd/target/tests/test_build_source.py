import json
import os
import stat
import subprocess
from textwrap import dedent

import responses
from fixtures import FakeLogger, TempDir
from systemfixtures import FakeFilesystem
from testtools.matchers import AnyMatch, MatchesAll, MatchesListwise
from testtools.testcase import TestCase

from lpbuildd.target.backend import InvalidBuildFilePath
from lpbuildd.target.build_source import (
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


class TestBuildSource(TestCase):
    def test_run_build_command_no_env(self):
        args = [
            "build-source",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_source = parse_args(args=args).operation
        build_source.run_build_command(["echo", "hello world"])
        self.assertThat(
            build_source.backend.run.calls,
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
            "build-source",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_source = parse_args(args=args).operation
        build_source.run_build_command(
            ["echo", "hello world"], env={"FOO": "bar baz"}
        )
        self.assertThat(
            build_source.backend.run.calls,
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
            "build-source",
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
        build_source = parse_args(args=args).operation
        build_source.install()
        self.assertThat(
            build_source.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet(
                        "install", "bzr"
                    ),
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
            "build-source",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_source = parse_args(args=args).operation
        build_source.install()
        self.assertThat(
            build_source.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet(
                        "install", "bzr"
                    ),
                    RanSnap("install", "--classic", "--channel=latest/edge/craftctl", "sourcecraft"),
                    RanCommand(["mkdir", "-p", "/home/buildd"]),
                ]
            ),
        )

    def test_install_git(self):
        args = [
            "build-source",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "test-image",
        ]
        build_source = parse_args(args=args).operation
        build_source.install()
        self.assertThat(
            build_source.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet(
                        "install", "git"
                    ),
                    RanSnap("install", "--classic", "--channel=latest/edge/craftctl", "sourcecraft"),
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
            "build-source",
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
        build_source = parse_args(args=args).operation
        build_source.install()
        self.assertThat(
            build_source.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet(
                        "install", "git"
                    ),
                    RanCommand(
                        ["snap", "ack", "/dev/stdin"],
                        input_text=store_assertion,
                    ),
                    RanCommand(
                        ["snap", "set", "core", "proxy.store=store-id"]
                    ),
                    RanSnap("install", "--classic", "--channel=latest/edge/craftctl", "sourcecraft"),
                    RanCommand(["mkdir", "-p", "/home/buildd"]),
                ]
            ),
        )

    def test_install_proxy(self):
        args = [
            "build-source",
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
        build_source = parse_args(args=args).operation
        build_source.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/lpbuildd-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_source.install()
        self.assertThat(
            build_source.backend.run.calls,
            MatchesListwise(
                [
                    RanAptGet(
                        "install",
                        "python3",
                        "socat",
                        "git",
                    ),
                    RanSnap("install", "--classic", "--channel=latest/edge/craftctl", "sourcecraft"),
                    RanCommand(["mkdir", "-p", "/home/buildd"]),
                ]
            ),
        )
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_source.backend.backend_fs[
                "/usr/local/bin/lpbuildd-git-proxy"
            ],
        )

    def test_repo_bzr(self):
        args = [
            "build-source",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_source = parse_args(args=args).operation
        build_source.backend.build_path = self.useFixture(TempDir()).path
        build_source.backend.run = FakeRevisionID("42")
        build_source.repo()
        self.assertThat(
            build_source.backend.run.calls,
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
        status_path = os.path.join(build_source.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "42"}, json.load(status))

    def test_repo_git(self):
        args = [
            "build-source",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--git-repository",
            "lp:foo",
            "test-image",
        ]
        build_source = parse_args(args=args).operation
        build_source.backend.build_path = self.useFixture(TempDir()).path
        build_source.backend.run = FakeRevisionID("0" * 40)
        build_source.repo()
        self.assertThat(
            build_source.backend.run.calls,
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
        status_path = os.path.join(build_source.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_git_with_path(self):
        args = [
            "build-source",
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
        build_source = parse_args(args=args).operation
        build_source.backend.build_path = self.useFixture(TempDir()).path
        build_source.backend.run = FakeRevisionID("0" * 40)
        build_source.repo()
        self.assertThat(
            build_source.backend.run.calls,
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
        status_path = os.path.join(build_source.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_git_with_tag_path(self):
        args = [
            "build-source",
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
        build_source = parse_args(args=args).operation
        build_source.backend.build_path = self.useFixture(TempDir()).path
        build_source.backend.run = FakeRevisionID("0" * 40)
        build_source.repo()
        self.assertThat(
            build_source.backend.run.calls,
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
        status_path = os.path.join(build_source.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_proxy(self):
        args = [
            "build-source",
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
        build_source = parse_args(args=args).operation
        build_source.backend.build_path = self.useFixture(TempDir()).path
        build_source.backend.run = FakeRevisionID("0" * 40)
        build_source.repo()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/lpbuildd-git-proxy",
            "SNAPPY_STORE_NO_CDN": "1",
        }
        self.assertThat(
            build_source.backend.run.calls,
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
        status_path = os.path.join(build_source.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_build(self):
        args = [
            "build-source",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_source = parse_args(args=args).operation
        build_source.backend.add_dir("/build/test-directory")
        build_source.build()
        self.assertThat(
            build_source.backend.run.calls,
            MatchesListwise(
                [
                    RanBuildCommand(
                        ["sourcecraft", "pack", "-v", "--destructive-mode"],
                        cwd="/home/buildd/test-image/.",
                    ),
                ]
            ),
        )

    def test_build_with_path(self):
        args = [
            "build-source",
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
        build_source = parse_args(args=args).operation
        build_source.backend.add_dir("/build/test-directory")
        build_source.build()
        self.assertThat(
            build_source.backend.run.calls,
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
            "build-source",
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
        build_source = parse_args(args=args).operation
        build_source.build()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/lpbuildd-git-proxy",
            "SNAPPY_STORE_NO_CDN": "1",
        }
        self.assertThat(
            build_source.backend.run.calls,
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
            "build-source",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_source = parse_args(args=args).operation
        build_source.backend.build_path = self.useFixture(TempDir()).path
        build_source.backend.run = FakeRevisionID("42")
        self.assertEqual(0, build_source.run())
        self.assertThat(
            build_source.backend.run.calls,
            MatchesAll(
                AnyMatch(
                    RanAptGet(
                        "install", "bzr"
                    ),
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
            "build-source",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_source = parse_args(args=args).operation
        build_source.backend.run = FailInstall()
        self.assertEqual(RETCODE_FAILURE_INSTALL, build_source.run())

    def test_run_repo_fails(self):
        class FailRepo(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super().__call__(run_args, *args, **kwargs)
                if run_args[:2] == ["bzr", "branch"]:
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-source",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_source = parse_args(args=args).operation
        build_source.backend.run = FailRepo()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_source.run())

    def test_run_build_fails(self):
        class FailBuild(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super().__call__(run_args, *args, **kwargs)
                if run_args[0] == "sourcecraft":
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-source",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--branch",
            "lp:foo",
            "test-image",
        ]
        build_source = parse_args(args=args).operation
        build_source.backend.build_path = self.useFixture(TempDir()).path
        build_source.backend.run = FailBuild()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_source.run())

    def test_build_with_invalid_build_path_parent(self):
        args = [
            "build-source",
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
        build_source = parse_args(args=args).operation
        build_source.backend.add_dir("/build/test-directory")
        self.assertRaises(InvalidBuildFilePath, build_source.build)

    def test_build_with_invalid_build_path_absolute(self):
        args = [
            "build-source",
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
        build_source = parse_args(args=args).operation
        build_source.backend.add_dir("/build/test-directory")
        self.assertRaises(InvalidBuildFilePath, build_source.build)

    def test_build_with_invalid_build_path_symlink(self):
        args = [
            "build-source",
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
        build_source = parse_args(args=args).operation
        build_source.buildd_path = self.useFixture(TempDir()).path
        os.symlink(
            "/etc/hosts", os.path.join(build_source.buildd_path, "build")
        )
        self.assertRaises(InvalidBuildFilePath, build_source.build)
