# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import json
import os
import stat
import subprocess
from textwrap import dedent

from fixtures import (
    FakeLogger,
    TempDir,
    )
import responses
from systemfixtures import FakeFilesystem
from testtools.matchers import (
    AnyMatch,
    Equals,
    Is,
    MatchesAll,
    MatchesDict,
    MatchesListwise,
    )
from testtools.testcase import TestCase

from lpbuildd.target.backend import InvalidBuildFilePath
from lpbuildd.target.build_charm import (
    RETCODE_FAILURE_BUILD,
    RETCODE_FAILURE_INSTALL,
    )
from lpbuildd.tests.fakebuilder import FakeMethod
from lpbuildd.target.tests.test_build_snap import (
    FakeRevisionID,
    RanSnap,
    )
from lpbuildd.target.cli import parse_args


class RanCommand(MatchesListwise):

    def __init__(self, args, echo=None, cwd=None, input_text=None,
                 get_output=None, universal_newlines=None, **env):
        kwargs_matcher = {}
        if echo is not None:
            kwargs_matcher["echo"] = Is(echo)
        if cwd:
            kwargs_matcher["cwd"] = Equals(cwd)
        if input_text:
            kwargs_matcher["input_text"] = Equals(input_text)
        if get_output is not None:
            kwargs_matcher["get_output"] = Is(get_output)
        if universal_newlines is not None:
            kwargs_matcher["universal_newlines"] = Is(universal_newlines)
        if env:
            kwargs_matcher["env"] = MatchesDict(
                {key: Equals(value) for key, value in env.items()})
        super(RanCommand, self).__init__(
            [Equals((args,)), MatchesDict(kwargs_matcher)])


class RanAptGet(RanCommand):

    def __init__(self, *args):
        super(RanAptGet, self).__init__(["apt-get", "-y"] + list(args))


class RanBuildCommand(RanCommand):

    def __init__(self, args, **kwargs):
        kwargs.setdefault("LANG", "C.UTF-8")
        kwargs.setdefault("SHELL", "/bin/sh")
        super(RanBuildCommand, self).__init__(args, **kwargs)


class TestBuildCharm(TestCase):

    def test_run_build_command_no_env(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.run_build_command(["echo", "hello world"])
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["echo", "hello world"],
                cwd="/home/buildd/test-image"),
            ]))

    def test_run_build_command_env(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.run_build_command(
            ["echo", "hello world"], env={"FOO": "bar baz"})
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["echo", "hello world"],
                FOO="bar baz",
                cwd="/home/buildd/test-image")
            ]))

    def test_install_channels(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--channel=core=candidate", "--channel=core18=beta",
            "--channel=charmcraft=edge",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanAptGet("install", "bzr"),
            RanSnap("install", "--channel=candidate", "core"),
            RanSnap("install", "--channel=beta", "core18"),
            RanSnap("install", "--classic", "--channel=edge", "charmcraft"),
            RanCommand(["mkdir", "-p", "/home/buildd"]),
            ]))

    def test_install_bzr(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image"
            ]
        build_charm = parse_args(args=args).operation
        build_charm.install()
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanAptGet("install", "bzr"),
            RanSnap("install", "--classic", "charmcraft"),
            RanCommand(["mkdir", "-p", "/home/buildd"]),
            ]))

    def test_install_git(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "test-image"
            ]
        build_charm = parse_args(args=args).operation
        build_charm.install()
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanAptGet("install", "git"),
            RanSnap("install", "--classic", "charmcraft"),
            RanCommand(["mkdir", "-p", "/home/buildd"]),
            ]))

    @responses.activate
    def test_install_snap_store_proxy(self):
        store_assertion = dedent("""\
            type: store
            store: store-id
            url: http://snap-store-proxy.example

            body
            """)

        def respond(request):
            return 200, {"X-Assertion-Store-Id": "store-id"}, store_assertion

        responses.add_callback(
            "GET", "http://snap-store-proxy.example/v2/auth/store/assertions",
            callback=respond)
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo",
            "--snap-store-proxy-url", "http://snap-store-proxy.example/",
            "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.install()
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanAptGet("install", "git"),
            RanCommand(
                ["snap", "ack", "/dev/stdin"], input_text=store_assertion),
            RanCommand(["snap", "set", "core", "proxy.store=store-id"]),
            RanSnap("install", "--classic", "charmcraft"),
            RanCommand(["mkdir", "-p", "/home/buildd"]),
            ]))

    def test_install_proxy(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo",
            "--proxy-url", "http://proxy.example:3128/",
            "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/snap-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_charm.install()
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanAptGet("install", "python3", "socat", "git"),
            RanSnap("install", "--classic", "charmcraft"),
            RanCommand(["mkdir", "-p", "/home/buildd"]),
            ]))
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_charm.backend.backend_fs["/usr/local/bin/snap-git-proxy"])

    def test_repo_bzr(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.build_path = self.useFixture(TempDir()).path
        build_charm.backend.run = FakeRevisionID("42")
        build_charm.repo()
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["bzr", "branch", "lp:foo", "test-image"], cwd="/home/buildd"),
            RanBuildCommand(
                ["bzr", "revno"],
                cwd="/home/buildd/test-image", get_output=True,
                universal_newlines=True),
            ]))
        status_path = os.path.join(build_charm.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "42"}, json.load(status))

    def test_repo_git(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.build_path = self.useFixture(TempDir()).path
        build_charm.backend.run = FakeRevisionID("0" * 40)
        build_charm.repo()
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "lp:foo", "test-image"], cwd="/home/buildd"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image"),
            RanBuildCommand(
                ["git", "rev-parse", "HEAD^{}"],
                cwd="/home/buildd/test-image",
                get_output=True, universal_newlines=True),
            ]))
        status_path = os.path.join(build_charm.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_git_with_path(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "--git-path", "next", "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.build_path = self.useFixture(TempDir()).path
        build_charm.backend.run = FakeRevisionID("0" * 40)
        build_charm.repo()
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "-b", "next", "lp:foo", "test-image"],
                cwd="/home/buildd"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image"),
            RanBuildCommand(
                ["git", "rev-parse", "next^{}"],
                cwd="/home/buildd/test-image", get_output=True,
                universal_newlines=True),
            ]))
        status_path = os.path.join(build_charm.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_git_with_tag_path(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "--git-path", "refs/tags/1.0",
            "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.build_path = self.useFixture(TempDir()).path
        build_charm.backend.run = FakeRevisionID("0" * 40)
        build_charm.repo()
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "-b", "1.0", "lp:foo", "test-image"],
                cwd="/home/buildd"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image"),
            RanBuildCommand(
                ["git", "rev-parse", "refs/tags/1.0^{}"],
                cwd="/home/buildd/test-image", get_output=True,
                universal_newlines=True),
            ]))
        status_path = os.path.join(build_charm.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_proxy(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo",
            "--proxy-url", "http://proxy.example:3128/",
            "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.build_path = self.useFixture(TempDir()).path
        build_charm.backend.run = FakeRevisionID("0" * 40)
        build_charm.repo()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/snap-git-proxy",
            }
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "lp:foo", "test-image"],
                cwd="/home/buildd", **env),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image", **env),
            RanBuildCommand(
                ["git", "rev-parse", "HEAD^{}"],
                cwd="/home/buildd/test-image", get_output=True,
                universal_newlines=True),
            ]))
        status_path = os.path.join(build_charm.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_build(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.add_dir('/build/test-directory')
        build_charm.build()
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["charmcraft", "build", "-v", "-f",
                 "/home/buildd/test-image/."],
                cwd="/home/buildd/test-image"),
            ]))

    def test_build_with_path(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-path", "build-aux/",
            "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.add_dir('/build/test-directory')
        build_charm.build()
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["charmcraft", "build", "-v", "-f",
                 "/home/buildd/test-image/build-aux/"],
                cwd="/home/buildd/test-image"),
            ]))

    def test_build_proxy(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--proxy-url", "http://proxy.example:3128/",
            "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.build()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/snap-git-proxy",
            }
        self.assertThat(build_charm.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["charmcraft", "build", "-v", "-f",
                 "/home/buildd/test-image/."],
                cwd="/home/buildd/test-image", **env),
            ]))

    def test_run_succeeds(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.build_path = self.useFixture(TempDir()).path
        build_charm.backend.run = FakeRevisionID("42")
        self.assertEqual(0, build_charm.run())
        self.assertThat(build_charm.backend.run.calls, MatchesAll(
            AnyMatch(RanAptGet("install", "bzr"),),
            AnyMatch(RanBuildCommand(
                ["bzr", "branch", "lp:foo", "test-image"],
                cwd="/home/buildd")),
            AnyMatch(RanBuildCommand(
                ["charmcraft", "build", "-v", "-f",
                 "/home/buildd/test-image/."],
                cwd="/home/buildd/test-image")),
            ))

    def test_run_install_fails(self):
        class FailInstall(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailInstall, self).__call__(run_args, *args, **kwargs)
                if run_args[0] == "apt-get":
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.run = FailInstall()
        self.assertEqual(RETCODE_FAILURE_INSTALL, build_charm.run())

    def test_run_repo_fails(self):
        class FailRepo(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailRepo, self).__call__(run_args, *args, **kwargs)
                if run_args[:2] == ["bzr", "branch"]:
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.run = FailRepo()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_charm.run())

    def test_run_build_fails(self):
        class FailBuild(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailBuild, self).__call__(run_args, *args, **kwargs)
                if run_args[0] == "charmcraft":
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.build_path = self.useFixture(TempDir()).path
        build_charm.backend.run = FailBuild()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_charm.run())

    def test_build_with_invalid_build_path_parent(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-path", "../",
            "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.add_dir('/build/test-directory')
        self.assertRaises(InvalidBuildFilePath, build_charm.build)

    def test_build_with_invalid_build_path_absolute(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-path", "/etc",
            "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.backend.add_dir('/build/test-directory')
        self.assertRaises(InvalidBuildFilePath, build_charm.build)

    def test_build_with_invalid_build_path_symlink(self):
        args = [
            "build-charm",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-path", "build/",
            "test-image",
            ]
        build_charm = parse_args(args=args).operation
        build_charm.buildd_path = self.useFixture(TempDir()).path
        os.symlink(
            '/etc/hosts',
            os.path.join(build_charm.buildd_path, 'build'))
        self.assertRaises(InvalidBuildFilePath, build_charm.build)
