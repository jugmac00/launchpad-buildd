# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

from contextlib import closing
import io
import json
import os.path
import tarfile
from textwrap import dedent
try:
    from unittest import mock
except ImportError:
    import mock

from fixtures import (
    EnvironmentVariable,
    MockPatch,
    TempDir,
    )
import pylxd
from pylxd.exceptions import LXDAPIException
from systemfixtures import (
    FakeFilesystem,
    FakeProcesses,
    )
from testtools import TestCase
from testtools.matchers import (
    DirContains,
    Equals,
    FileContains,
    HasPermissions,
    MatchesDict,
    MatchesListwise,
    )

from lpbuildd.target.lxd import (
    LXD,
    policy_rc_d,
    )


LXD_RUNNING = 103


class FakeLXDAPIException(LXDAPIException):

    def __init__(self):
        super(FakeLXDAPIException, self).__init__(None)

    def __str__(self):
        return "Fake LXD exception"


class TestLXD(TestCase):

    def make_chroot_tarball(self, output_path):
        source = self.useFixture(TempDir()).path
        hello = os.path.join(source, "bin", "hello")
        os.mkdir(os.path.dirname(hello))
        with open(hello, "w") as f:
            f.write("hello\n")
            os.fchmod(f.fileno(), 0o755)
        with tarfile.open(output_path, "w:bz2") as tar:
            tar.add(source, arcname="chroot-autobuild")

    def test_convert(self):
        tmp = self.useFixture(TempDir()).path
        source_tarball_path = os.path.join(tmp, "source.tar.bz2")
        target_tarball_path = os.path.join(tmp, "target.tar.gz")
        self.make_chroot_tarball(source_tarball_path)
        with tarfile.open(source_tarball_path, "r") as source_tarball:
            creation_time = source_tarball.getmember("chroot-autobuild").mtime
            with tarfile.open(target_tarball_path, "w:gz") as target_tarball:
                LXD("1", "xenial", "amd64")._convert(
                    source_tarball, target_tarball)

        target = os.path.join(tmp, "target")
        with tarfile.open(target_tarball_path, "r") as target_tarball:
            target_tarball.extractall(path=target)
        self.assertThat(target, DirContains(["metadata.yaml", "rootfs"]))
        with open(os.path.join(target, "metadata.yaml")) as metadata_file:
            metadata = json.load(metadata_file)
        self.assertThat(metadata, MatchesDict({
            "architecture": Equals("x86_64"),
            "creation_date": Equals(creation_time),
            "properties": MatchesDict({
                "os": Equals("Ubuntu"),
                "series": Equals("xenial"),
                "architecture": Equals("amd64"),
                "description": Equals(
                    "Launchpad chroot for Ubuntu xenial (amd64)"),
                }),
            }))
        rootfs = os.path.join(target, "rootfs")
        self.assertThat(rootfs, DirContains(["bin"]))
        self.assertThat(os.path.join(rootfs, "bin"), DirContains(["hello"]))
        hello = os.path.join(rootfs, "bin", "hello")
        self.assertThat(hello, FileContains("hello\n"))
        self.assertThat(hello, HasPermissions("0755"))

    def test_create(self):
        tmp = self.useFixture(TempDir()).path
        source_tarball_path = os.path.join(tmp, "source.tar.bz2")
        self.make_chroot_tarball(source_tarball_path)
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        client.images.all.return_value = []
        image = mock.MagicMock()
        client.images.create.return_value = image
        LXD("1", "xenial", "amd64").create(source_tarball_path)

        client.images.create.assert_called_once_with(mock.ANY, wait=True)
        with io.BytesIO(client.images.create.call_args[0][0]) as f:
            with tarfile.open(fileobj=f) as tar:
                with closing(tar.extractfile("rootfs/bin/hello")) as hello:
                    self.assertEqual("hello\n", hello.read())
        image.add_alias.assert_called_once_with(
            "lp-xenial-amd64", "lp-xenial-amd64")

    def test_start(self):
        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/sys")
        fs_fixture.add("/run")
        os.makedirs("/run/launchpad-buildd")
        fs_fixture.add("/etc")
        os.mkdir("/etc")
        for name in ("hosts", "hostname", "resolv.conf"):
            path = os.path.join("/etc", name)
            with open(path, "w") as f:
                f.write("host %s\n" % name)
            os.chmod(path, 0o644)
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        client.profiles.get.side_effect = FakeLXDAPIException
        container = client.containers.create.return_value
        client.containers.get.return_value = container
        container.start.side_effect = (
            lambda wait=False: setattr(container, "status_code", LXD_RUNNING))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        LXD("1", "xenial", "amd64").start()

        client.profiles.get.assert_called_once_with("lpbuildd")
        expected_config = {
            "security.privileged": "true",
            "security.nesting": "true",
            "raw.lxc": dedent("""\
                lxc.aa_profile=unconfined
                lxc.cgroup.devices.deny=
                lxc.cgroup.devices.allow=
                lxc.mount.auto=
                lxc.mount.auto=proc:rw sys:rw
                lxc.network.0.ipv4=10.10.10.2/24
                lxc.network.0.ipv4.gateway=10.10.10.1
                """),
            }
        expected_devices = {
            "eth0": {
                "name": "eth0",
                "nictype": "bridged",
                "parent": "lpbuilddbr0",
                "type": "nic",
                },
            }
        client.profiles.create.assert_called_once_with(
            "lpbuildd", expected_config, expected_devices)

        ip = ["sudo", "ip"]
        iptables = ["sudo", "iptables", "-w"]
        iptables_comment = [
            "-m", "comment", "--comment", "managed by launchpad-buildd"]
        self.assertThat(
            [proc._args["args"] for proc in processes_fixture.procs],
            MatchesListwise([
                Equals(ip + ["link", "add", "dev", "lpbuilddbr0",
                             "type", "bridge"]),
                Equals(ip + ["addr", "add", "10.10.10.1/24",
                             "dev", "lpbuilddbr0"]),
                Equals(ip + ["link", "set", "dev", "lpbuilddbr0", "up"]),
                Equals(
                    ["sudo", "sysctl", "-q", "-w", "net.ipv4.ip_forward=1"]),
                Equals(
                    iptables +
                    ["-t", "nat", "-A", "POSTROUTING",
                     "-s", "10.10.10.1/24", "!", "-d", "10.10.10.1/24",
                     "-j", "MASQUERADE"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-I", "INPUT", "-i", "lpbuilddbr0",
                     "-p", "udp", "--dport", "53", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-I", "INPUT", "-i", "lpbuilddbr0",
                     "-p", "tcp", "--dport", "53", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-I", "FORWARD", "-i", "lpbuilddbr0", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-I", "FORWARD", "-o", "lpbuilddbr0", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    ["sudo", "/usr/sbin/dnsmasq", "-s", "lpbuildd",
                     "-S", "/lpbuildd/", "-u", "buildd", "--strict-order",
                     "--bind-interfaces",
                     "--pid-file=/run/launchpad-buildd/dnsmasq.pid",
                     "--except-interface=lo", "--interface=lpbuilddbr0",
                     "--listen-address=10.10.10.1"]),
                ]))

        client.containers.create.assert_called_once_with({
            "name": "lp-xenial-amd64",
            "profiles": ["default", "lpbuildd"],
            "source": {"type": "image", "alias": "lp-xenial-amd64"},
            }, wait=True)
        container.api.files.post.assert_any_call(
            params={"path": "/etc/hosts"},
            data=b"host hosts\n",
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0644"})
        container.api.files.post.assert_any_call(
            params={"path": "/etc/hostname"},
            data=b"host hostname\n",
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0644"})
        container.api.files.post.assert_any_call(
            params={"path": "/etc/resolv.conf"},
            data=b"host resolv.conf\n",
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0644"})
        container.api.files.post.assert_any_call(
            params={"path": "/usr/local/sbin/policy-rc.d"},
            data=policy_rc_d.encode("UTF-8"),
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0755"})
        container.start.assert_called_once_with(wait=True)
        self.assertEqual(LXD_RUNNING, container.status_code)

    def test_run(self):
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="lxc")
        LXD("1", "xenial", "amd64").run(
            ["apt-get", "update"], env={"LANG": "C"})

        expected_args = [
            ["lxc", "exec", "lp-xenial-amd64", "--env", "LANG=C", "--",
             "linux64", "apt-get", "update"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_run_get_output(self):
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(
            lambda _: {"stdout": io.BytesIO(b"hello\n")}, name="lxc")
        self.assertEqual(
            "hello\n",
            LXD("1", "xenial", "amd64").run(
                ["echo", "hello"], get_output=True))

        expected_args = [
            ["lxc", "exec", "lp-xenial-amd64", "--",
             "linux64", "echo", "hello"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_copy_in(self):
        source_dir = self.useFixture(TempDir()).path
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        container = mock.MagicMock()
        client.containers.get.return_value = container
        source_path = os.path.join(source_dir, "source")
        with open(source_path, "w") as source_file:
            source_file.write("hello\n")
        os.chmod(source_path, 0o644)
        target_path = "/path/to/target"
        LXD("1", "xenial", "amd64").copy_in(source_path, target_path)

        client.containers.get.assert_called_once_with("lp-xenial-amd64")
        container.api.files.post.assert_called_once_with(
            params={"path": target_path},
            data=b"hello\n",
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0644"})

    def test_copy_out(self):
        target_dir = self.useFixture(TempDir()).path
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        container = mock.MagicMock()
        client.containers.get.return_value = container
        container.api.files.get.return_value.iter_content.return_value = (
            iter([b"hello\n", b"world\n"]))
        source_path = "/path/to/source"
        target_path = os.path.join(target_dir, "target")
        LXD("1", "xenial", "amd64").copy_out(source_path, target_path)

        client.containers.get.assert_called_once_with("lp-xenial-amd64")
        container.api.files.get.assert_called_once_with(
            params={"path": source_path}, stream=True)
        self.assertThat(target_path, FileContains("hello\nworld\n"))

    def test_path_exists(self):
        processes_fixture = self.useFixture(FakeProcesses())
        test_proc_infos = iter([{}, {"returncode": 1}])
        processes_fixture.add(lambda _: next(test_proc_infos), name="lxc")
        self.assertTrue(LXD("1", "xenial", "amd64").path_exists("/present"))
        self.assertFalse(LXD("1", "xenial", "amd64").path_exists("/absent"))

        expected_args = [
            ["lxc", "exec", "lp-xenial-amd64", "--",
             "linux64", "test", "-e", path]
            for path in ("/present", "/absent")
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_islink(self):
        processes_fixture = self.useFixture(FakeProcesses())
        test_proc_infos = iter([{}, {"returncode": 1}])
        processes_fixture.add(lambda _: next(test_proc_infos), name="lxc")
        self.assertTrue(LXD("1", "xenial", "amd64").islink("/link"))
        self.assertFalse(LXD("1", "xenial", "amd64").islink("/file"))

        expected_args = [
            ["lxc", "exec", "lp-xenial-amd64", "--",
             "linux64", "test", "-h", path]
            for path in ("/link", "/file")
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_listdir(self):
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(
            lambda _: {"stdout": io.BytesIO(b"foo\0bar\0baz\0")}, name="lxc")
        self.assertEqual(
            ["foo", "bar", "baz"],
            LXD("1", "xenial", "amd64").listdir("/path"))

        expected_args = [
            ["lxc", "exec", "lp-xenial-amd64", "--",
             "linux64", "find", "/path", "-mindepth", "1", "-maxdepth", "1",
             "-printf", "%P\\0"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_stop(self):
        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/sys")
        os.makedirs("/sys/class/net/lpbuilddbr0")
        fs_fixture.add("/run")
        os.makedirs("/run/launchpad-buildd")
        with open("/run/launchpad-buildd/dnsmasq.pid", "w") as f:
            f.write("42\n")
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        container = client.containers.get('lp-xenial-amd64')
        container.status_code = LXD_RUNNING
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        LXD("1", "xenial", "amd64").stop()

        container.stop.assert_called_once_with(wait=True)
        container.delete.assert_called_once_with(wait=True)
        ip = ["sudo", "ip"]
        iptables = ["sudo", "iptables", "-w"]
        iptables_comment = [
            "-m", "comment", "--comment", "managed by launchpad-buildd"]
        self.assertThat(
            [proc._args["args"] for proc in processes_fixture.procs],
            MatchesListwise([
                Equals(ip + ["addr", "flush", "dev", "lpbuilddbr0"]),
                Equals(ip + ["link", "set", "dev", "lpbuilddbr0", "down"]),
                Equals(
                    iptables +
                    ["-D", "INPUT", "-i", "lpbuilddbr0",
                     "-p", "udp", "--dport", "53", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-D", "INPUT", "-i", "lpbuilddbr0",
                     "-p", "tcp", "--dport", "53", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-D", "FORWARD", "-i", "lpbuilddbr0", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-D", "FORWARD", "-o", "lpbuilddbr0", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-t", "nat", "-D", "POSTROUTING",
                     "-s", "10.10.10.1/24", "!", "-d", "10.10.10.1/24",
                     "-j", "MASQUERADE"] +
                    iptables_comment),
                Equals(["sudo", "kill", "-9", "42"]),
                Equals(ip + ["link", "delete", "lpbuilddbr0"]),
                ]))

    def test_remove(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        self.useFixture(MockPatch("pylxd.Client"))
        other_image = mock.MagicMock()
        other_image.aliases = []
        image = mock.MagicMock()
        image.aliases = [{"name": "lp-xenial-amd64"}]
        client = pylxd.Client()
        client.images.all.return_value = [other_image, image]
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        LXD("1", "xenial", "amd64").remove()

        other_image.delete.assert_not_called()
        image.delete.assert_called_once_with(wait=True)
        self.assertThat(
            [proc._args["args"] for proc in processes_fixture.procs],
            MatchesListwise([
                Equals(["sudo", "rm", "-rf", "/expected/home/build-1"]),
                ]))
