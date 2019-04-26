# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import json
import os.path
import stat
import subprocess

from fixtures import (
    FakeLogger,
    TempDir,
    )
from systemfixtures import FakeFilesystem
from testtools import TestCase
from testtools.matchers import (
    AnyMatch,
    Equals,
    Is,
    MatchesAll,
    MatchesDict,
    MatchesListwise,
    )

from lpbuildd.target.build_snap import (
    RETCODE_FAILURE_BUILD,
    RETCODE_FAILURE_INSTALL,
    )
from lpbuildd.target.cli import parse_args
from lpbuildd.tests.fakebuilder import FakeMethod


class RanCommand(MatchesListwise):

    def __init__(self, args, get_output=None, echo=None, cwd=None, **env):
        kwargs_matcher = {}
        if get_output is not None:
            kwargs_matcher["get_output"] = Is(get_output)
        if echo is not None:
            kwargs_matcher["echo"] = Is(echo)
        if cwd:
            kwargs_matcher["cwd"] = Equals(cwd)
        if env:
            kwargs_matcher["env"] = MatchesDict(
                {key: Equals(value) for key, value in env.items()})
        super(RanCommand, self).__init__(
            [Equals((args,)), MatchesDict(kwargs_matcher)])


class RanAptGet(RanCommand):

    def __init__(self, *args):
        super(RanAptGet, self).__init__(["apt-get", "-y"] + list(args))


class RanSnap(RanCommand):

    def __init__(self, *args):
        super(RanSnap, self).__init__(["snap"] + list(args))


class RanBuildCommand(RanCommand):

    def __init__(self, args, **kwargs):
        kwargs.setdefault("LANG", "C.UTF-8")
        kwargs.setdefault("SHELL", "/bin/sh")
        super(RanBuildCommand, self).__init__(args, **kwargs)


class FakeRevisionID(FakeMethod):

    def __init__(self, revision_id):
        super(FakeRevisionID, self).__init__()
        self.revision_id = revision_id

    def __call__(self, run_args, *args, **kwargs):
        super(FakeRevisionID, self).__call__(run_args, *args, **kwargs)
        if (run_args[:2] == ["bzr", "revno"] or
                (run_args[0] == "git" and "rev-parse" in run_args)):
            return "%s\n" % self.revision_id


class TestBuildSnap(TestCase):

    def test_run_build_command_no_env(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.run_build_command(["echo", "hello world"])
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(["echo", "hello world"]),
            ]))

    def test_run_build_command_env(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.run_build_command(
            ["echo", "hello world"], env={"FOO": "bar baz"})
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(["echo", "hello world"], FOO="bar baz"),
            ]))

    def test_install_bzr(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap"
            ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanAptGet("install", "bzr", "snapcraft"),
            ]))

    def test_install_git(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "test-snap"
            ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanAptGet("install", "git", "snapcraft"),
            ]))

    def test_install_proxy(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo",
            "--proxy-url", "http://proxy.example:3128/",
            "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/snap-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_snap.install()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanAptGet("install", "git", "python3", "socat", "snapcraft"),
            RanCommand(["mkdir", "-p", "/root/.subversion"]),
            ]))
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_snap.backend.backend_fs["/usr/local/bin/snap-git-proxy"])
        self.assertEqual(
            (b"[global]\n"
             b"http-proxy-host = proxy.example\n"
             b"http-proxy-port = 3128\n",
             stat.S_IFREG | 0o644),
            build_snap.backend.backend_fs["/root/.subversion/servers"])

    def test_install_channels(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--channel=core=candidate", "--channel=core18=beta",
            "--channel=snapcraft=edge",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanAptGet("install", "bzr", "sudo"),
            RanSnap("install", "--channel=candidate", "core"),
            RanSnap("install", "--channel=beta", "core18"),
            RanSnap("install", "--classic", "--channel=edge", "snapcraft"),
            ]))

    def test_repo_bzr(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("42")
        build_snap.repo()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["bzr", "branch", "lp:foo", "test-snap"], cwd="/build"),
            RanBuildCommand(
                ["bzr", "revno"], cwd="/build/test-snap", get_output=True),
            ]))
        status_path = os.path.join(build_snap.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "42"}, json.load(status))

    def test_repo_git(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("0" * 40)
        build_snap.repo()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "lp:foo", "test-snap"], cwd="/build"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/build/test-snap"),
            RanBuildCommand(
                ["git", "rev-parse", "HEAD^{}"],
                cwd="/build/test-snap", get_output=True),
            ]))
        status_path = os.path.join(build_snap.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_git_with_path(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "--git-path", "next", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("0" * 40)
        build_snap.repo()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "-b", "next", "lp:foo", "test-snap"],
                cwd="/build"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/build/test-snap"),
            RanBuildCommand(
                ["git", "rev-parse", "next^{}"],
                cwd="/build/test-snap", get_output=True),
            ]))
        status_path = os.path.join(build_snap.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_git_with_tag_path(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "--git-path", "refs/tags/1.0",
            "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("0" * 40)
        build_snap.repo()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "-b", "1.0", "lp:foo", "test-snap"],
                cwd="/build"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/build/test-snap"),
            RanBuildCommand(
                ["git", "rev-parse", "refs/tags/1.0^{}"],
                cwd="/build/test-snap", get_output=True),
            ]))
        status_path = os.path.join(build_snap.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_repo_proxy(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo",
            "--proxy-url", "http://proxy.example:3128/",
            "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("0" * 40)
        build_snap.repo()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/snap-git-proxy",
            }
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "lp:foo", "test-snap"], cwd="/build", **env),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/build/test-snap", **env),
            RanBuildCommand(
                ["git", "rev-parse", "HEAD^{}"],
                cwd="/build/test-snap", get_output=True),
            ]))
        status_path = os.path.join(build_snap.backend.build_path, "status")
        with open(status_path) as status:
            self.assertEqual({"revision_id": "0" * 40}, json.load(status))

    def test_pull(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap",
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
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["snapcraft", "pull"], cwd="/build/test-snap", **env),
            ]))

    def test_pull_proxy(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--build-url", "https://launchpad.example/build",
            "--branch", "lp:foo", "--proxy-url", "http://proxy.example:3128/",
            "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.pull()
        env = {
            "SNAPCRAFT_LOCAL_SOURCES": "1",
            "SNAPCRAFT_SETUP_CORE": "1",
            "SNAPCRAFT_BUILD_INFO": "1",
            "SNAPCRAFT_IMAGE_INFO": (
                '{"build_url": "https://launchpad.example/build"}'),
            "SNAPCRAFT_BUILD_ENVIRONMENT": "host",
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/snap-git-proxy",
            }
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["snapcraft", "pull"], cwd="/build/test-snap", **env),
            ]))

    def test_pull_build_source_tarball(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-source-tarball", "test-snap",
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
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["snapcraft", "pull"], cwd="/build/test-snap", **env),
            RanBuildCommand(
                ["tar", "-czf", "test-snap.tar.gz",
                 "--format=gnu", "--sort=name", "--exclude-vcs",
                 "--numeric-owner", "--owner=0", "--group=0",
                 "test-snap"],
                cwd="/build"),
            ]))

    def test_pull_private(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--private", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.pull()
        env = {
            "SNAPCRAFT_LOCAL_SOURCES": "1",
            "SNAPCRAFT_SETUP_CORE": "1",
            "SNAPCRAFT_IMAGE_INFO": "{}",
            "SNAPCRAFT_BUILD_ENVIRONMENT": "host",
            }
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["snapcraft", "pull"], cwd="/build/test-snap", **env),
            ]))

    def test_build(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.build()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["snapcraft"], cwd="/build/test-snap",
                SNAPCRAFT_BUILD_INFO="1", SNAPCRAFT_IMAGE_INFO="{}",
                SNAPCRAFT_BUILD_ENVIRONMENT="host"),
            ]))

    def test_build_proxy(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--build-url", "https://launchpad.example/build",
            "--branch", "lp:foo", "--proxy-url", "http://proxy.example:3128/",
            "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.build()
        env = {
            "SNAPCRAFT_BUILD_INFO": "1",
            "SNAPCRAFT_IMAGE_INFO": (
                '{"build_url": "https://launchpad.example/build"}'),
            "SNAPCRAFT_BUILD_ENVIRONMENT": "host",
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/snap-git-proxy",
            }
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(["snapcraft"], cwd="/build/test-snap", **env),
            ]))

    def test_build_private(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--private", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.build()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["snapcraft"], cwd="/build/test-snap",
                SNAPCRAFT_IMAGE_INFO="{}", SNAPCRAFT_BUILD_ENVIRONMENT="host"),
            ]))

    def test_build_including_build_request_id(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--build-request-id", "13", "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.build()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["snapcraft"], cwd="/build/test-snap",
                SNAPCRAFT_BUILD_INFO="1",
                SNAPCRAFT_IMAGE_INFO='{"build-request-id": "13"}',
                SNAPCRAFT_BUILD_ENVIRONMENT="host"),
            ]))

    def test_build_including_build_request_timestamp(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--build-request-timestamp", "2018-04-13T14:50:02Z",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.build()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["snapcraft"], cwd="/build/test-snap",
                SNAPCRAFT_BUILD_INFO="1",
                SNAPCRAFT_IMAGE_INFO=(
                    '{"build-request-timestamp": "2018-04-13T14:50:02Z"}'),
                SNAPCRAFT_BUILD_ENVIRONMENT="host"),
            ]))

    # XXX cjwatson 2017-08-07: Test revoke_token.  It may be easiest to
    # convert it to requests first.

    def test_run_succeeds(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--build-request-id", "13",
            "--build-url", "https://launchpad.example/build",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("42")
        self.assertEqual(0, build_snap.run())
        self.assertThat(build_snap.backend.run.calls, MatchesAll(
            AnyMatch(RanAptGet("install", "bzr", "snapcraft")),
            AnyMatch(RanBuildCommand(
                ["bzr", "branch", "lp:foo", "test-snap"], cwd="/build")),
            AnyMatch(RanBuildCommand(
                ["snapcraft", "pull"], cwd="/build/test-snap",
                SNAPCRAFT_LOCAL_SOURCES="1", SNAPCRAFT_SETUP_CORE="1",
                SNAPCRAFT_BUILD_INFO="1",
                SNAPCRAFT_IMAGE_INFO=(
                    '{"build-request-id": "13",'
                    ' "build_url": "https://launchpad.example/build"}'),
                SNAPCRAFT_BUILD_ENVIRONMENT="host")),
            AnyMatch(RanBuildCommand(
                ["snapcraft"], cwd="/build/test-snap",
                SNAPCRAFT_BUILD_INFO="1",
                SNAPCRAFT_IMAGE_INFO=(
                    '{"build-request-id": "13",'
                    ' "build_url": "https://launchpad.example/build"}'),
                SNAPCRAFT_BUILD_ENVIRONMENT="host")),
            ))

    def test_run_install_fails(self):
        class FailInstall(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailInstall, self).__call__(run_args, *args, **kwargs)
                if run_args[0] == "apt-get":
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.run = FailInstall()
        self.assertEqual(RETCODE_FAILURE_INSTALL, build_snap.run())

    def test_run_repo_fails(self):
        class FailRepo(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailRepo, self).__call__(run_args, *args, **kwargs)
                if run_args[:2] == ["bzr", "branch"]:
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.run = FailRepo()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_snap.run())

    def test_run_pull_fails(self):
        class FailPull(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailPull, self).__call__(run_args, *args, **kwargs)
                if run_args[:2] == ["bzr", "revno"]:
                    return "42\n"
                elif run_args[:2] == ["snapcraft", "pull"]:
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FailPull()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_snap.run())

    def test_run_build_fails(self):
        class FailBuild(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailBuild, self).__call__(run_args, *args, **kwargs)
                if run_args[:2] == ["bzr", "revno"]:
                    return "42\n"
                elif run_args == ["snapcraft"]:
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FailBuild()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_snap.run())
