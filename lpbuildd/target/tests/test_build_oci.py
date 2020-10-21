# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import datetime
import json
try:
    from unittest import mock
except ImportError:
    import mock
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

from lpbuildd.target.build_oci import (
    InvalidBuildFilePath,
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


class TestBuildOCIManifestGeneration(TestCase):
    def test_getSecurityManifestContent(self):
        now = datetime.datetime.now().isoformat()
        metadata = {
            "architectures": ["amd64", "386"],
            "recipe_owner": dict(name="pappacena", email="me@foo.com"),
            "build_request_id": 123,
            "build_request_timestamp": now,
            "build_requester": dict(name="someone", email="someone@foo.com"),
            "build_urls": {
                "amd64": "http://lp.net/build/1",
                "386": "http://lp.net/build/2"
            },
        }
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:git-repo", "--git-path", "refs/heads/main",
            "--build-arg", "VAR1=1", "--build-arg", "VAR2=22",
            "--build-file", "SomeDockerfile", "--build-path", "docker/builder",
            "--metadata", json.dumps(metadata),
            "test-image"
        ]
        build_oci = parse_args(args=args).operation

        # Expected build_oci.backend.run outputs.
        commit_hash = b"a1b2c3d4e5f5"
        grep_dctrl_output = dedent("""
        Package: adduser
        Version: 3.118
        
        Package: apt
        Version: 1.8.2.1

        Package: util-linux
        Version: 2.33.1
        
        Package: zlib1g
        Version: 1:1.2.11
        Source: zlib
        """).encode('utf8')

        os_release_cat_output = dedent("""
        NAME="Ubuntu"
        VERSION="20.04.1 LTS (Focal Fossa)"
        ID=ubuntu
        ID_LIKE=debian
        PRETTY_NAME="Ubuntu 20.04.1 LTS"
        VERSION_ID="20.04"
        HOME_URL="https://www.ubuntu.com/"
        SUPPORT_URL="https://help.ubuntu.com/"
        BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
        PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
        VERSION_CODENAME=focal
        UBUNTU_CODENAME=focal
        """).encode("utf8")

        # Side effect for "docker cp...", "dgrep-dctrl" and "git rev-parse..."
        build_oci.backend.run = mock.Mock(side_effect=[
            # docker cp and dgrep-dctrl to get packages.
            None, grep_dctrl_output,
            # git rev-parse HEAD to get current revision.
            commit_hash,
            # docker cp and cat for container /etc/os-release.
            None, os_release_cat_output])

        self.assertEqual(build_oci._getSecurityManifestContent(), {
            "manifest-version": "1",
            "name": "test-image",
            'os-release-id': "ubuntu",
            'os-release-version-id': "20.04",
            "architectures": ["amd64", "386"],
            "publisher-emails": ["me@foo.com", "someone@foo.com"],
            "image-info": {
                "build-request-id": 123,
                "build-request-timestamp": now,
                "build-urls": {
                    "386": "http://lp.net/build/2",
                    "amd64": "http://lp.net/build/1"}},
            "vcs-info": [{
                "source": "lp:git-repo",
                "source-branch": "refs/heads/main",
                "source-build-args": ["VAR1=1", "VAR2=22"],
                "source-build-file": "SomeDockerfile",
                "source-commit": "a1b2c3d4e5f5",
                "source-subdir": "docker/builder"
            }],
            "packages": [
                {'package': 'adduser', 'source': None, 'version': '3.118'},
                {'package': 'apt', 'source': None, 'version': '1.8.2.1'},
                {'package': 'util-linux', 'source': None, 'version': '2.33.1'},
                {'package': 'zlib1g', 'source': 'zlib', 'version': '1:1.2.11'}
            ]
        })

    def test_getSecurityManifestContent_without_manifest(self):
        """With minimal parameters, the manifest content should give
        something back without breaking."""
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:git-repo", "--git-path", "refs/heads/main",
            "test-image"
        ]

        # Here we will not mock the package gathering nor os-release file
        # reading in order to let it raise exception, so we end up with a
        # manifest without packages.
        build_oci = parse_args(args=args).operation

        self.assertEqual(build_oci._getSecurityManifestContent(), {
            "manifest-version": "1",
            "name": "test-image",
            'os-release-id': None,
            'os-release-version-id': None,
            "architectures": ["amd64"],
            "publisher-emails": [],
            "image-info": {
                "build-request-id": None,
                "build-request-timestamp": None,
                "build-urls": {}},
            "vcs-info": [{
                "source": "lp:git-repo",
                "source-branch": "refs/heads/main",
                "source-build-args": [],
                "source-build-file": None,
                "source-commit": None,
                "source-subdir": "."
            }],
            "packages": []
        })

    def test_getContainerPackageList(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:git-repo", "--git-path", "refs/heads/main",
            "test-image"
        ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.run = mock.Mock(return_value=dedent("""
        Package: adduser
        Version: 3.118
        
        Package: apt
        Version: 1.8.2.1

        Package: util-linux
        Version: 2.33.1-0.1
        
        Package: zlib1g
        Version: 1:1.2.11
        Source: zlib
        """).encode("utf8"))
        self.assertEqual([
            {'package': 'adduser', 'source': None, 'version': '3.118'},
            {'package': 'apt', 'source': None, 'version': '1.8.2.1'},
            {'package': 'util-linux', 'source': None, 'version': '2.33.1-0.1'},
            {'package': 'zlib1g', 'source': 'zlib', 'version': '1:1.2.11'}
        ], build_oci._getContainerPackageList())

class TestBuildOCI(TestCase):

    def test_run_build_command_no_env(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.run_build_command(["echo", "hello world"])
        self.assertThat(build_oci.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["echo", "hello world"],
                cwd="/home/buildd/test-image"),
            ]))

    def test_run_build_command_env(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.run_build_command(
            ["echo", "hello world"], env={"FOO": "bar baz"})
        self.assertThat(build_oci.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["echo", "hello world"],
                FOO="bar baz",
                cwd="/home/buildd/test-image")
            ]))

    def test_install_bzr(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image"
            ]
        build_oci = parse_args(args=args).operation
        build_oci.install()
        self.assertThat(build_oci.backend.run.calls, MatchesListwise([
            RanAptGet("install", "bzr", "docker.io", "dctrl-tools"),
            RanCommand(["systemctl", "restart", "docker"]),
            RanCommand(["mkdir", "-p", "/home/buildd"]),
            ]))

    def test_install_git(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "test-image"
            ]
        build_oci = parse_args(args=args).operation
        build_oci.install()
        self.assertThat(build_oci.backend.run.calls, MatchesListwise([
            RanAptGet("install", "git", "docker.io", "dctrl-tools"),
            RanCommand(["systemctl", "restart", "docker"]),
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
            "buildsnap",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo",
            "--snap-store-proxy-url", "http://snap-store-proxy.example/",
            "test-snap",
            ]
        build_snap = parse_args(args=args).operation
        build_snap.install()
        self.assertThat(build_snap.backend.run.calls, MatchesListwise([
            RanAptGet("install", "git", "snapcraft"),
            RanCommand(
                ["snap", "ack", "/dev/stdin"], input_text=store_assertion),
            RanCommand(["snap", "set", "core", "proxy.store=store-id"]),
            ]))

    def test_install_proxy(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo",
            "--proxy-url", "http://proxy.example:3128/",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.bin = "/builderbin"
        self.useFixture(FakeFilesystem()).add("/builderbin")
        os.mkdir("/builderbin")
        with open("/builderbin/snap-git-proxy", "w") as proxy_script:
            proxy_script.write("proxy script\n")
            os.fchmod(proxy_script.fileno(), 0o755)
        build_oci.install()
        self.assertThat(build_oci.backend.run.calls, MatchesListwise([
            RanCommand(
                ["mkdir", "-p", "/etc/systemd/system/docker.service.d"]),
            RanAptGet("install", "python3", "socat", "git", "docker.io",
                      "dctrl-tools"),
            RanCommand(["systemctl", "restart", "docker"]),
            RanCommand(["mkdir", "-p", "/home/buildd"]),
            ]))
        self.assertEqual(
            (b"proxy script\n", stat.S_IFREG | 0o755),
            build_oci.backend.backend_fs["/usr/local/bin/snap-git-proxy"])

    def test_repo_bzr(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.build_path = self.useFixture(TempDir()).path
        build_oci.backend.run = FakeMethod()
        build_oci.repo()
        self.assertThat(build_oci.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["bzr", "branch", "lp:foo", "test-image"], cwd="/home/buildd"),
            ]))

    def test_repo_git(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.build_path = self.useFixture(TempDir()).path
        build_oci.backend.run = FakeMethod()
        build_oci.repo()
        self.assertThat(build_oci.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "lp:foo", "test-image"], cwd="/home/buildd"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image"),
            ]))

    def test_repo_git_with_path(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "--git-path", "next", "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.build_path = self.useFixture(TempDir()).path
        build_oci.backend.run = FakeMethod()
        build_oci.repo()
        self.assertThat(build_oci.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "-b", "next", "lp:foo", "test-image"],
                cwd="/home/buildd"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image"),
            ]))

    def test_repo_git_with_tag_path(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo", "--git-path", "refs/tags/1.0",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.build_path = self.useFixture(TempDir()).path
        build_oci.backend.run = FakeMethod()
        build_oci.repo()
        self.assertThat(build_oci.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "-b", "1.0", "lp:foo", "test-image"],
                cwd="/home/buildd"),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image"),
            ]))

    def test_repo_proxy(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--git-repository", "lp:foo",
            "--proxy-url", "http://proxy.example:3128/",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.build_path = self.useFixture(TempDir()).path
        build_oci.backend.run = FakeMethod()
        build_oci.repo()
        env = {
            "http_proxy": "http://proxy.example:3128/",
            "https_proxy": "http://proxy.example:3128/",
            "GIT_PROXY_COMMAND": "/usr/local/bin/snap-git-proxy",
            }
        self.assertThat(build_oci.backend.run.calls, MatchesListwise([
            RanBuildCommand(
                ["git", "clone", "lp:foo", "test-image"],
                cwd="/home/buildd", **env),
            RanBuildCommand(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd="/home/buildd/test-image", **env),
            ]))

    def assertRanPostBuildCommands(self, build_oci):
        rev_num_args = (
            ['bzr', 'revno'] if build_oci.args.branch
            else ['git', 'rev-parse', 'HEAD'])
        self.assertThat(build_oci.backend.run.calls[1:], MatchesListwise([
            RanBuildCommand(
                ['docker', 'create', '--name', 'test-image', 'test-image'],
                cwd="/home/buildd/test-image"),
            RanCommand(['mkdir', '-p', '/tmp/image-root-dir/.rocks']),

            # Manifest building: packages discovery.
            RanBuildCommand([
                'docker', 'cp', '-L',
                'test-image:/var/lib/dpkg/status', '/tmp/dpkg-status'],
                cwd="/home/buildd/test-image"),
            RanCommand([
                'grep-dctrl', '-s', 'Package,Version,Source', '',
                '/tmp/dpkg-status'], get_output=True),

            # Manifest building: get current revision number.
            RanCommand(
                rev_num_args, cwd="/home/buildd/test-image", get_output=True),

            # Manifest building: os-release file.
            RanBuildCommand([
                'docker', 'cp',  '-L', 'test-image:/etc/os-release',
                '/tmp/os-release'],
                cwd="/home/buildd/test-image"),
            RanCommand(['cat', '/tmp/os-release'], get_output=True),

            # Filesystem injection and image commiting.
            RanBuildCommand(
                ['docker', 'cp', '/tmp/image-root-dir/.', 'test-image:/'],
                cwd="/home/buildd/test-image"),
            RanBuildCommand(
                ['docker', 'commit', 'test-image', 'test-image'],
                cwd="/home/buildd/test-image"),
            RanBuildCommand(
                ['docker', 'rm', 'test-image'],
                cwd="/home/buildd/test-image"),
        ]))

    def test_build(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.add_dir('/build/test-directory')
        build_oci.build()
        self.assertThat(build_oci.backend.run.calls[0], RanBuildCommand(
            ["docker", "build", "--no-cache", "--tag", "test-image",
             "/home/buildd/test-image/."],
            cwd="/home/buildd/test-image"))
        self.assertRanPostBuildCommands(build_oci)

    def test_build_with_file(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-file", "build-aux/Dockerfile",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.add_dir('/build/test-directory')
        build_oci.build()
        self.assertThat(build_oci.backend.run.calls[0], RanBuildCommand(
            ["docker", "build", "--no-cache", "--tag", "test-image",
             "--file", "./build-aux/Dockerfile",
             "/home/buildd/test-image/."],
            cwd="/home/buildd/test-image"))
        self.assertRanPostBuildCommands(build_oci)

    def test_build_with_path(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-path", "a-sub-directory/",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.add_dir('/build/test-directory')
        build_oci.build()
        self.assertThat(build_oci.backend.run.calls[0], RanBuildCommand(
            ["docker", "build", "--no-cache", "--tag", "test-image",
             "/home/buildd/test-image/a-sub-directory/"],
            cwd="/home/buildd/test-image"))
        self.assertRanPostBuildCommands(build_oci)

    def test_build_with_file_and_path(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-file", "build-aux/Dockerfile",
            "--build-path", "test-build-path",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.add_dir('/build/test-directory')
        build_oci.build()
        self.assertThat(build_oci.backend.run.calls[0], RanBuildCommand(
            ["docker", "build", "--no-cache", "--tag", "test-image",
             "--file", "test-build-path/build-aux/Dockerfile",
             "/home/buildd/test-image/test-build-path"],
            cwd="/home/buildd/test-image"))
        self.assertRanPostBuildCommands(build_oci)

    def test_build_with_args(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-file", "build-aux/Dockerfile",
            "--build-path", "test-build-path",
            "--build-arg=VAR1=xxx", "--build-arg=VAR2=yyy",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.add_dir('/build/test-directory')
        build_oci.build()
        self.assertThat(build_oci.backend.run.calls[0], RanBuildCommand(
            ["docker", "build", "--no-cache", "--tag", "test-image",
             "--file", "test-build-path/build-aux/Dockerfile",
             "--build-arg=VAR1=xxx", "--build-arg=VAR2=yyy",
             "/home/buildd/test-image/test-build-path"],
            cwd="/home/buildd/test-image"))
        self.assertRanPostBuildCommands(build_oci)

    def test_build_proxy(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--proxy-url", "http://proxy.example:3128/",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.add_dir('/build/test-directory')
        build_oci.build()
        self.assertThat(build_oci.backend.run.calls[0], RanBuildCommand(
            ["docker", "build", "--no-cache",
             "--build-arg", "http_proxy=http://proxy.example:3128/",
             "--build-arg", "https_proxy=http://proxy.example:3128/",
             "--tag", "test-image", "/home/buildd/test-image/."],
            cwd="/home/buildd/test-image"))
        self.assertRanPostBuildCommands(build_oci)

    def test_run_succeeds(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.build_path = self.useFixture(TempDir()).path
        build_oci.backend.run = FakeMethod()
        self.assertEqual(0, build_oci.run())
        self.assertThat(build_oci.backend.run.calls, MatchesAll(
            AnyMatch(RanAptGet("install", "bzr", "docker.io", "dctrl-tools")),
            AnyMatch(RanBuildCommand(
                ["bzr", "branch", "lp:foo", "test-image"],
                cwd="/home/buildd")),
            AnyMatch(RanBuildCommand(
                ["docker", "build", "--no-cache", "--tag", "test-image",
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
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.run = FailInstall()
        self.assertEqual(RETCODE_FAILURE_INSTALL, build_oci.run())

    def test_run_repo_fails(self):
        class FailRepo(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailRepo, self).__call__(run_args, *args, **kwargs)
                if run_args[:2] == ["bzr", "branch"]:
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.run = FailRepo()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_oci.run())

    def test_run_build_fails(self):
        class FailBuild(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super(FailBuild, self).__call__(run_args, *args, **kwargs)
                if run_args[0] == "docker":
                    raise subprocess.CalledProcessError(1, run_args)

        self.useFixture(FakeLogger())
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.build_path = self.useFixture(TempDir()).path
        build_oci.backend.run = FailBuild()
        self.assertEqual(RETCODE_FAILURE_BUILD, build_oci.run())

    def test_build_with_invalid_file_path_parent(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-file", "../build-aux/Dockerfile",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.add_dir('/build/test-directory')
        self.assertRaises(InvalidBuildFilePath, build_oci.build)

    def test_build_with_invalid_file_path_absolute(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-file", "/etc/Dockerfile",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.add_dir('/build/test-directory')
        self.assertRaises(InvalidBuildFilePath, build_oci.build)

    def test_build_with_invalid_file_path_symlink(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-file", "Dockerfile",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.buildd_path = self.useFixture(TempDir()).path
        os.symlink(
            '/etc/hosts',
            os.path.join(build_oci.buildd_path, 'Dockerfile'))
        self.assertRaises(InvalidBuildFilePath, build_oci.build)

    def test_build_with_invalid_build_path_parent(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-path", "../",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.add_dir('/build/test-directory')
        self.assertRaises(InvalidBuildFilePath, build_oci.build)

    def test_build_with_invalid_build_path_absolute(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-path", "/etc",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.backend.add_dir('/build/test-directory')
        self.assertRaises(InvalidBuildFilePath, build_oci.build)

    def test_build_with_invalid_build_path_symlink(self):
        args = [
            "build-oci",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "--branch", "lp:foo", "--build-path", "build/",
            "test-image",
            ]
        build_oci = parse_args(args=args).operation
        build_oci.buildd_path = self.useFixture(TempDir()).path
        os.symlink(
            '/etc/hosts',
            os.path.join(build_oci.buildd_path, 'build'))
        self.assertRaises(InvalidBuildFilePath, build_oci.build)
