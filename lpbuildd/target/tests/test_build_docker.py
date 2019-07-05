# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os.path
import stat
import subprocess
from textwrap import dedent

from fixtures import (
    FakeLogger,
    TempDir,
    )
import responses
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

from lpbuildd.target.build_docker import (
    RETCODE_FAILURE_BUILD,
    RETCODE_FAILURE_INSTALL,
    )
from lpbuildd.target.cli import parse_args
from lpbuildd.tests.fakebuilder import FakeMethod


class RanCommand(MatchesListwise):

    def __init__(self, args, echo=None, cwd=None, input_text=None,
                 get_output=None, **env):
        kwargs_matcher = {}
        if echo is not None:
            kwargs_matcher["echo"] = Is(echo)
        if cwd:
            kwargs_matcher["cwd"] = Equals(cwd)
        if input_text:
            kwargs_matcher["input_text"] = Equals(input_text)
        if get_output is not None:
            kwargs_matcher["get_output"] = Is(get_output)
        if env:
            kwargs_matcher["env"] = MatchesDict(
                {key: Equals(value) for key, value in env.items()})
        super(RanCommand, self).__init__(
            [Equals((args,)), MatchesDict(kwargs_matcher)])


class RanAptGet(RanCommand):

    def __init__(self, *args):
        super(RanAptGet, self).__init__(["apt-get", "-y"] + list(args))


class RanSnap(RanCommand):

    def __init__(self, *args, **kwargs):
        super(RanSnap, self).__init__(["snap"] + list(args), **kwargs)


class RanBuildCommand(RanCommand):

    def __init__(self, args, **kwargs):
        kwargs.setdefault("LANG", "C.UTF-8")
        kwargs.setdefault("SHELL", "/bin/sh")
        super(RanBuildCommand, self).__init__(args, **kwargs)


class TestBuildDocker(TestCase):

    def test_run_build_command_no_env(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.run_build_command(["echo", "hello world"])
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanBuildCommand(["echo", "hello world"]),
            ]))

    def test_run_build_command_env(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.run_build_command(
            ["echo", "hello world"], env={"FOO": "bar baz"})
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanBuildCommand(["echo", "hello world"], FOO="bar baz"),
            ]))

    def test_install_bzr(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image"
            ]
        build_docker = parse_args(args=args).operation
        build_docker.install()
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanAptGet("install", "bzr"),
            RanSnap("install", "docker"),
            RanCommand(["mkdir", "-p", "/home/buildd"]),
            ]))

    def test_install_git(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "test-image"
            ]
        build_docker = parse_args(args=args).operation
        build_docker.install()
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanAptGet("install", "git"),
            RanSnap("install", "docker"),
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
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo",
            "--snap-store-proxy-url", "http://snap-store-proxy.example/",
            "test-image",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanAptGet("install", "git"),
            RanSnap("ack", "/dev/stdin", input_text=store_assertion),
            RanSnap("set", "core", "proxy.store=store-id"),
            RanSnap("install", "docker"),
            RanCommand(["mkdir", "-p", "/home/buildd"]),
            ]))

    def test_install_proxy(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo",
            "--proxy-url", "http://proxy.example:3128/",
            "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/snap-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_docker.install()
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanAptGet("install", "git", "python3", "socat"),
            RanSnap("install", "docker"),
            RanCommand(["mkdir", "-p", "/home/buildd"]),
            ]))
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_docker.backend.backend_fs["/usr/local/bin/snap-git-proxy"])

    def test_repo_bzr(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.build_path = self.useFixture(TempDir()).path
        build_docker.backend.run = FakeMethod()
        build_docker.repo()
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["bzr", "branch", "lp:foo", "test-image"], cwd="/home/buildd"),
            ]))

    def test_repo_git(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.build_path = self.useFixture(TempDir()).path
        build_docker.backend.run = FakeMethod()
        build_docker.repo()
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "lp:foo", "test-image"], cwd="/home/buildd"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image"),
            ]))

    def test_repo_git_with_path(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "--git-path", "next", "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.build_path = self.useFixture(TempDir()).path
        build_docker.backend.run = FakeMethod()
        build_docker.repo()
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "-b", "next", "lp:foo", "test-image"],
                cwd="/home/buildd"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image"),
            ]))

    def test_repo_git_with_tag_path(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "--git-path", "refs/tags/1.0",
            "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.build_path = self.useFixture(TempDir()).path
        build_docker.backend.run = FakeMethod()
        build_docker.repo()
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "-b", "1.0", "lp:foo", "test-image"],
                cwd="/home/buildd"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image"),
            ]))

    def test_repo_proxy(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo",
            "--proxy-url", "http://proxy.example:3128/",
            "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.build_path = self.useFixture(TempDir()).path
        build_docker.backend.run = FakeMethod()
        build_docker.repo()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/snap-git-proxy",
            }
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "lp:foo", "test-image"],
                cwd="/home/buildd", **env),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image", **env),
            ]))

    def test_build(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.add_dir('/build/test-directory')
        build_docker.build()
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["docker", "build", "--no-cache", "--tag", "test-image",
                 "/home/buildd/test-image"]),
            RanCommand(["mkdir", "-p", "/home/buildd/test-image-extract"]),
            RanBuildCommand([
                '/bin/bash', '-c',
                'docker save test-image > /build/test-image.tar']),
            RanBuildCommand([
                'tar', '-xf', '/build/test-image.tar', '-C', '/build/']),
            RanBuildCommand([
                'tar', '-cvf', '/build/test-directory.tar',
                '/build/test-directory']),
            ]))

    def test_build_with_file(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--file", "build-aux/Dockerfile",
            "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.add_dir('/build/test-directory')
        build_docker.build()
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["docker", "build", "--no-cache", "--tag", "test-image",
                 "--file", "build-aux/Dockerfile", "/home/buildd/test-image"]),
            RanCommand(["mkdir", "-p", "/home/buildd/test-image-extract"]),
            RanBuildCommand([
                '/bin/bash', '-c',
                'docker save test-image > /build/test-image.tar']),
            RanBuildCommand([
                'tar', '-xf', '/build/test-image.tar', '-C', '/build/']),
            RanBuildCommand([
                'tar', '-cvf', '/build/test-directory.tar',
                '/build/test-directory']),
            ]))

    def test_build_proxy(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--proxy-url", "http://proxy.example:3128/",
            "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.add_dir('/build/test-directory')
        build_docker.build()
        self.assertThat(build_docker.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["docker", "build", "--no-cache",
                 "--build-arg", "http_proxy=http://proxy.example:3128/",
                 "--build-arg", "https_proxy=http://proxy.example:3128/",
                 "--tag", "test-image", "/home/buildd/test-image"]),
            RanCommand(["mkdir", "-p", "/home/buildd/test-image-extract"]),
            RanBuildCommand([
                '/bin/bash', '-c',
                'docker save test-image > /build/test-image.tar']),
            RanBuildCommand([
                'tar', '-xf', '/build/test-image.tar', '-C', '/build/']),
            RanBuildCommand([
                'tar', '-cvf', '/build/test-directory.tar',
                '/build/test-directory']),
            ]))

    def test_run_succeeds(self):
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.build_path = self.useFixture(TempDir()).path
        build_docker.backend.run = FakeMethod()
        self.assertEqual(0, build_docker.run())
        self.assertThat(build_docker.backend.run.calls, MatchesAll(
            AnyMatch(RanAptGet("install", "bzr")),
            AnyMatch(RanSnap("install", "docker")),
            AnyMatch(RanBuildCommand(
                ["bzr", "branch", "lp:foo", "test-image"],
                cwd="/home/buildd")),
            AnyMatch(RanBuildCommand(
                ["docker", "build", "--no-cache", "--tag", "test-image",
                 "/home/buildd/test-image"])),
            ))

    def test_run_install_fails(self):
        class FailInstall(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailInstall, self).__call__(run_args, *args, **kwargs)
                if run_args[0] == "apt-get":
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.run = FailInstall()
        self.assertEqual(RETCODE_FAILURE_INSTALL, build_docker.run())

    def test_run_repo_fails(self):
        class FailRepo(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailRepo, self).__call__(run_args, *args, **kwargs)
                if run_args[:2] == ["bzr", "branch"]:
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.run = FailRepo()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_docker.run())

    def test_run_build_fails(self):
        class FailBuild(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailBuild, self).__call__(run_args, *args, **kwargs)
                if run_args[0] == "docker":
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-docker",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_docker = parse_args(args=args).operation
        build_docker.backend.build_path = self.useFixture(TempDir()).path
        build_docker.backend.run = FailBuild()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_docker.run())
