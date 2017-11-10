# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import subprocess

from fixtures import FakeLogger
from testtools import TestCase
from testtools.matchers import (
    AnyMatch,
    Equals,
    Is,
    MatchesAll,
    MatchesDict,
    MatchesListwise,
    )

from lpbuildd.target.build_livefs import (
    RETCODE_FAILURE_BUILD,
    RETCODE_FAILURE_INSTALL,
    )
from lpbuildd.target.cli import parse_args
from lpbuildd.tests.fakeslave import FakeMethod


class RanCommand(MatchesListwise):

    def __init__(self, args, echo=None, cwd=None, **env):
        kwargs_matcher = {}
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


class RanBuildCommand(RanCommand):

    def __init__(self, args, **kwargs):
        super(RanBuildCommand, self).__init__(args, cwd="/build", **kwargs)


class TestBuildLiveFS(TestCase):

    def test_run_build_command_no_env(self):
        args = [
            "buildlivefs",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            ]
        build_livefs = parse_args(args=args).operation
        build_livefs.run_build_command(["echo", "hello world"])
        self.assertThat(build_livefs.backend.run.calls, MatchesListwise([
            RanBuildCommand(["echo", "hello world"]),
            ]))

    def test_run_build_command_env(self):
        args = [
            "buildlivefs",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            ]
        build_livefs = parse_args(args=args).operation
        build_livefs.run_build_command(
            ["echo", "hello world"], env={"FOO": "bar baz"})
        self.assertThat(build_livefs.backend.run.calls, MatchesListwise([
            RanBuildCommand(["echo", "hello world"], FOO="bar baz"),
            ]))

    def test_install(self):
        args = [
            "buildlivefs",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            ]
        build_livefs = parse_args(args=args).operation
        build_livefs.install()
        self.assertThat(build_livefs.backend.run.calls, MatchesListwise([
            RanAptGet("install", "livecd-rootfs"),
            ]))

    def test_install_i386(self):
        args = [
            "buildlivefs",
            "--backend=fake", "--series=xenial", "--arch=i386", "1",
            ]
        build_livefs = parse_args(args=args).operation
        build_livefs.install()
        self.assertThat(build_livefs.backend.run.calls, MatchesListwise([
            RanAptGet("install", "livecd-rootfs"),
            RanAptGet("--no-install-recommends", "install", "ltsp-server"),
            ]))

    def test_install_locale(self):
        args = [
            "buildlivefs",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--locale=zh_CN",
            ]
        build_livefs = parse_args(args=args).operation
        build_livefs.install()
        self.assertThat(build_livefs.backend.run.calls, MatchesListwise([
            RanAptGet("install", "livecd-rootfs"),
            RanAptGet(
                "--install-recommends", "install", "ubuntu-defaults-builder"),
            ]))

    def test_build(self):
        args = [
            "buildlivefs",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--project=ubuntu",
            ]
        build_livefs = parse_args(args=args).operation
        build_livefs.build()
        self.assertThat(build_livefs.backend.run.calls, MatchesListwise([
            RanBuildCommand(["rm", "-rf", "auto", "local"]),
            RanBuildCommand(["mkdir", "-p", "auto"]),
            RanBuildCommand(
                ["ln", "-s",
                 "/usr/share/livecd-rootfs/live-build/auto/config", "auto/"]),
            RanBuildCommand(
                ["ln", "-s",
                 "/usr/share/livecd-rootfs/live-build/auto/build", "auto/"]),
            RanBuildCommand(
                ["ln", "-s",
                 "/usr/share/livecd-rootfs/live-build/auto/clean", "auto/"]),
            RanBuildCommand(["lb", "clean", "--purge"]),
            RanBuildCommand(
                ["lb", "config"],
                PROJECT="ubuntu", ARCH="amd64", SUITE="xenial"),
            RanBuildCommand(["lb", "build"], PROJECT="ubuntu", ARCH="amd64"),
            ]))

    def test_build_locale(self):
        args = [
            "buildlivefs",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--locale=zh_CN",
            ]
        build_livefs = parse_args(args=args).operation
        build_livefs.build()
        self.assertThat(build_livefs.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["ubuntu-defaults-image", "--locale", "zh_CN",
                 "--arch", "amd64", "--release", "xenial"]),
            ]))

    def test_build_debug(self):
        args = [
            "buildlivefs",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--project=ubuntu", "--debug",
            ]
        build_livefs = parse_args(args=args).operation
        build_livefs.build()
        self.assertThat(build_livefs.backend.run.calls, MatchesListwise([
            RanBuildCommand(["rm", "-rf", "auto", "local"]),
            RanBuildCommand(["mkdir", "-p", "auto"]),
            RanBuildCommand(
                ["ln", "-s",
                 "/usr/share/livecd-rootfs/live-build/auto/config", "auto/"]),
            RanBuildCommand(
                ["ln", "-s",
                 "/usr/share/livecd-rootfs/live-build/auto/build", "auto/"]),
            RanBuildCommand(
                ["ln", "-s",
                 "/usr/share/livecd-rootfs/live-build/auto/clean", "auto/"]),
            RanBuildCommand(["mkdir", "-p", "local/functions"]),
            RanBuildCommand(
                ["sh", "-c", "echo 'set -x' >local/functions/debug.sh"]),
            RanBuildCommand(["lb", "clean", "--purge"]),
            RanBuildCommand(
                ["lb", "config"],
                PROJECT="ubuntu", ARCH="amd64", SUITE="xenial"),
            RanBuildCommand(["lb", "build"], PROJECT="ubuntu", ARCH="amd64"),
            ]))

    def test_run_succeeds(self):
        args = [
            "buildlivefs",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--project=ubuntu",
            ]
        build_livefs = parse_args(args=args).operation
        self.assertEqual(0, build_livefs.run())
        self.assertThat(build_livefs.backend.run.calls, MatchesAll(
            AnyMatch(RanAptGet("install", "livecd-rootfs")),
            AnyMatch(RanBuildCommand(
                ["lb", "build"], PROJECT="ubuntu", ARCH="amd64"))))

    def test_run_install_fails(self):
        class FailInstall(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailInstall, self).__call__(run_args, *args, **kwargs)
                if run_args[0] == "apt-get":
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "buildlivefs",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--project=ubuntu",
            ]
        build_livefs = parse_args(args=args).operation
        build_livefs.backend.run = FailInstall()
        self.assertEqual(RETCODE_FAILURE_INSTALL, build_livefs.run())

    def test_run_build_fails(self):
        class FailBuild(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailBuild, self).__call__(run_args, *args, **kwargs)
                if run_args[0] == "rm":
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "buildlivefs",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--project=ubuntu",
            ]
        build_livefs = parse_args(args=args).operation
        build_livefs.backend.run = FailBuild()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_livefs.run())
