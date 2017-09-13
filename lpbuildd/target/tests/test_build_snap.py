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
from lpbuildd.tests.fakeslave import FakeMethod


class RanCommand(MatchesListwise):

    def __init__(self, args, get_output=None, echo=None, **env):
        kwargs_matcher = {}
        if get_output is not None:
            kwargs_matcher["get_output"] = Is(get_output)
        if echo is not None:
            kwargs_matcher["echo"] = Is(echo)
        if env:
            kwargs_matcher["env"] = MatchesDict(env)
        super(RanCommand, self).__init__(
            [Equals((args,)), MatchesDict(kwargs_matcher)])


class RanAptGet(RanCommand):

    def __init__(self, *args):
        super(RanAptGet, self).__init__(["apt-get", "-y"] + list(args))


class RanBuildCommand(RanCommand):

    def __init__(self, command, path="/build", get_output=False):
        super(RanBuildCommand, self).__init__(
            ["/bin/sh", "-c", "cd %s && %s" % (path, command)],
            get_output=get_output, echo=False)


class FakeRevisionID(FakeMethod):

    def __init__(self, revision_id):
        super(FakeRevisionID, self).__init__()
        self.revision_id = revision_id

    def __call__(self, run_args, *args, **kwargs):
        super(FakeRevisionID, self).__call__(run_args, *args, **kwargs)
        if run_args[0] == "/bin/sh":
            command = run_args[2]
            if "bzr revno" in command or "rev-parse" in command:
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
            RanBuildCommand(
                "env LANG=C.UTF-8 SHELL=/bin/sh echo 'hello world'"),
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
            RanBuildCommand(
                "env LANG=C.UTF-8 SHELL=/bin/sh FOO='bar baz' "
                "echo 'hello world'"),
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
            RanAptGet("install", "snapcraft", "bzr"),
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
            RanAptGet("install", "snapcraft", "git"),
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
        build_snap.slavebin = "/slavebin"
        self.useFixture(FakeFilesystem()).add("/slavebin")
        os.mkdir("/slavebin")
        with open("/slavebin/snap-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_snap.install()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanAptGet("install", "snapcraft", "git", "python3", "socat"),
            ]))
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_snap.backend.backend_fs["/usr/local/bin/snap-git-proxy"])

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
        env = "env LANG=C.UTF-8 SHELL=/bin/sh "
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(env + "ls /build"),
            RanBuildCommand(env + "bzr branch lp:foo test-snap"),
            RanBuildCommand(env + "bzr revno test-snap", get_output=True),
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
        env = "env LANG=C.UTF-8 SHELL=/bin/sh "
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(env + "git clone lp:foo test-snap"),
            RanBuildCommand(
                env + "git -C test-snap submodule update --init --recursive"),
            RanBuildCommand(
                env + "git -C test-snap rev-parse HEAD", get_output=True),
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
        env = "env LANG=C.UTF-8 SHELL=/bin/sh "
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(env + "git clone -b next lp:foo test-snap"),
            RanBuildCommand(
                env + "git -C test-snap submodule update --init --recursive"),
            RanBuildCommand(
                env + "git -C test-snap rev-parse next", get_output=True),
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
        env = (
            "env LANG=C.UTF-8 SHELL=/bin/sh "
            "http_proxy=http://proxy.example:3128/ "
            "https_proxy=http://proxy.example:3128/ "
            "GIT_PROXY_COMMAND=/usr/local/bin/snap-git-proxy ")
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(env + "git clone lp:foo test-snap"),
            RanBuildCommand(
                env + "git -C test-snap submodule update --init --recursive"),
            RanBuildCommand(
                "env LANG=C.UTF-8 SHELL=/bin/sh "
                "git -C test-snap rev-parse HEAD",
                get_output=True),
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
        env = (
            "env LANG=C.UTF-8 SHELL=/bin/sh "
            "SNAPCRAFT_LOCAL_SOURCES=1 SNAPCRAFT_SETUP_CORE=1 ")
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(env + "snapcraft pull", path="/build/test-snap"),
            ]))

    def test_pull_proxy(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--proxy-url", "http://proxy.example:3128/",
            "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.pull()
        env = (
            "env LANG=C.UTF-8 SHELL=/bin/sh "
            "SNAPCRAFT_LOCAL_SOURCES=1 SNAPCRAFT_SETUP_CORE=1 "
            "http_proxy=http://proxy.example:3128/ "
            "https_proxy=http://proxy.example:3128/ "
            "GIT_PROXY_COMMAND=/usr/local/bin/snap-git-proxy ")
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(env + "snapcraft pull", path="/build/test-snap"),
            ]))

    def test_build(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.build()
        env = "env LANG=C.UTF-8 SHELL=/bin/sh "
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(env + "snapcraft", path="/build/test-snap"),
            ]))

    def test_build_proxy(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--proxy-url", "http://proxy.example:3128/",
            "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.build()
        env = (
            "env LANG=C.UTF-8 SHELL=/bin/sh "
            "http_proxy=http://proxy.example:3128/ "
            "https_proxy=http://proxy.example:3128/ "
            "GIT_PROXY_COMMAND=/usr/local/bin/snap-git-proxy ")
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanBuildCommand(env + "snapcraft", path="/build/test-snap"),
            ]))

    # XXX cjwatson 2017-08-07: Test revoke_token.  It may be easiest to
    # convert it to requests first.

    def test_run_succeeds(self):
        args = [
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.backend.build_path = self.useFixture(TempDir()).path
        build_snap.backend.run = FakeRevisionID("42")
        self.assertEqual(0, build_snap.run())
        self.assertThat(build_snap.backend.run.calls, MatchesAll(
            AnyMatch(RanAptGet("install", "snapcraft", "bzr")),
            AnyMatch(RanBuildCommand(
                "env LANG=C.UTF-8 SHELL=/bin/sh bzr branch lp:foo test-snap")),
            AnyMatch(RanBuildCommand(
                "env LANG=C.UTF-8 SHELL=/bin/sh "
                "SNAPCRAFT_LOCAL_SOURCES=1 SNAPCRAFT_SETUP_CORE=1 "
                "snapcraft pull", path="/build/test-snap")),
            AnyMatch(RanBuildCommand(
                "env LANG=C.UTF-8 SHELL=/bin/sh snapcraft",
                path="/build/test-snap")),
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
                if run_args[0] == "/bin/sh":
                    command = run_args[2]
                    if "bzr branch" in command:
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
                if run_args[0] == "/bin/sh":
                    command = run_args[2]
                    if "bzr revno" in command:
                        return "42\n"
                    elif "snapcraft pull" in command:
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
                if run_args[0] == "/bin/sh":
                    command = run_args[2]
                    if "bzr revno" in command:
                        return "42\n"
                    elif command.endswith(" snapcraft"):
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
