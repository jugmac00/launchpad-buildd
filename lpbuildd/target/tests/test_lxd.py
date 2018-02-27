# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

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
    LXDException,
    fallback_hosts,
    policy_rc_d,
    )


LXD_RUNNING = 103


class FakeLXDAPIException(LXDAPIException):

    def __init__(self):
        super(FakeLXDAPIException, self).__init__(None)

    def __str__(self):
        return "Fake LXD exception"


class FakeSessionGet:

    def __init__(self, file_contents):
        self.file_contents = file_contents

    def __call__(self, *args, **kwargs):
        params = kwargs["params"]
        response = mock.MagicMock()
        if params["path"] in self.file_contents:
            response.status_code = 200
            response.iter_content.return_value = iter(
                self.file_contents[params["path"]])
        else:
            response.json.return_value = {"error": "not found"}
        return response


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

    def assert_correct_profile(self, extra_raw_lxc_config=""):
        client = pylxd.Client()
        client.profiles.get.assert_called_once_with("lpbuildd")
        expected_config = {
            "security.privileged": "true",
            "security.nesting": "true",
            "raw.lxc": dedent("""\
                lxc.aa_profile=unconfined
                lxc.cap.drop=
                lxc.cap.drop=sys_time sys_module
                lxc.cgroup.devices.deny=
                lxc.cgroup.devices.allow=
                lxc.mount.auto=
                lxc.mount.auto=proc:rw sys:rw
                lxc.network.0.ipv4=10.10.10.2/24
                lxc.network.0.ipv4.gateway=10.10.10.1
                """) + extra_raw_lxc_config,
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

    def test_create_profile_amd64(self):
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        client.profiles.get.side_effect = FakeLXDAPIException
        LXD("1", "xenial", "amd64").create_profile()
        self.assert_correct_profile()

    def test_create_profile_powerpc(self):
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        client.profiles.get.side_effect = FakeLXDAPIException
        LXD("1", "xenial", "powerpc").create_profile()
        self.assert_correct_profile("lxc.seccomp=\n")

    def test_start(self):
        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/sys")
        fs_fixture.add("/run")
        os.makedirs("/run/launchpad-buildd")
        fs_fixture.add("/etc")
        os.mkdir("/etc")
        with open("/etc/resolv.conf", "w") as f:
            print("host resolv.conf", file=f)
        os.chmod("/etc/resolv.conf", 0o644)
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        client.profiles.get.side_effect = FakeLXDAPIException
        container = client.containers.create.return_value
        client.containers.get.return_value = container
        container.start.side_effect = (
            lambda wait=False: setattr(container, "status_code", LXD_RUNNING))
        files_api = container.api.files
        files_api._api_endpoint = "/1.0/containers/lp-xenial-amd64/files"
        files_api.session.get.side_effect = FakeSessionGet({
            "/etc/hosts": [b"127.0.0.1\tlocalhost\n"],
            })
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        processes_fixture.add(lambda _: {}, name="lxc")
        LXD("1", "xenial", "amd64").start()

        self.assert_correct_profile()

        ip = ["sudo", "ip"]
        iptables = ["sudo", "iptables", "-w"]
        iptables_comment = [
            "-m", "comment", "--comment", "managed by launchpad-buildd"]
        lxc = ["lxc", "exec", "lp-xenial-amd64", "--", "linux64"]
        expected_args = [
            Equals(ip + ["link", "add", "dev", "lpbuilddbr0",
                         "type", "bridge"]),
            Equals(ip + ["addr", "add", "10.10.10.1/24",
                         "dev", "lpbuilddbr0"]),
            Equals(ip + ["link", "set", "dev", "lpbuilddbr0", "up"]),
            Equals(["sudo", "sysctl", "-q", "-w", "net.ipv4.ip_forward=1"]),
            Equals(
                iptables +
                ["-t", "mangle", "-A", "FORWARD", "-i", "lpbuilddbr0",
                 "-p", "tcp", "--tcp-flags", "SYN,RST", "SYN",
                 "-j", "TCPMSS", "--clamp-mss-to-pmtu"] +
                iptables_comment),
            Equals(
                iptables +
                ["-t", "nat", "-A", "POSTROUTING",
                 "-s", "10.10.10.1/24", "!", "-d", "10.10.10.1/24",
                 "-j", "MASQUERADE"] +
                iptables_comment),
            Equals(
                ["sudo", "/usr/sbin/dnsmasq", "-s", "lpbuildd",
                 "-S", "/lpbuildd/", "-u", "buildd", "--strict-order",
                 "--bind-interfaces",
                 "--pid-file=/run/launchpad-buildd/dnsmasq.pid",
                 "--except-interface=lo", "--interface=lpbuilddbr0",
                 "--listen-address=10.10.10.1"]),
            Equals(
                lxc +
                ["mknod", "-m", "0660", "/dev/loop-control",
                 "c", "10", "237"]),
            ]
        for minor in range(8):
            expected_args.append(
                Equals(
                    lxc +
                    ["mknod", "-m", "0660", "/dev/loop%d" % minor,
                     "b", "7", str(minor)]))
        expected_args.append(
            Equals(
                lxc + ["mkdir", "-p", "/etc/systemd/system/snapd.service.d"]))
        self.assertThat(
            [proc._args["args"] for proc in processes_fixture.procs],
            MatchesListwise(expected_args))

        client.containers.create.assert_called_once_with({
            "name": "lp-xenial-amd64",
            "profiles": ["lpbuildd"],
            "source": {"type": "image", "alias": "lp-xenial-amd64"},
            }, wait=True)
        files_api.session.get.assert_any_call(
            "/1.0/containers/lp-xenial-amd64/files",
            params={"path": "/etc/hosts"}, stream=True)
        files_api.post.assert_any_call(
            params={"path": "/etc/hosts"},
            data=b"127.0.0.1\tlocalhost\n\n127.0.1.1\tlp-xenial-amd64\n",
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0644"})
        files_api.post.assert_any_call(
            params={"path": "/etc/hostname"},
            data=b"lp-xenial-amd64\n",
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0644"})
        files_api.post.assert_any_call(
            params={"path": "/etc/resolv.conf"},
            data=b"host resolv.conf\n",
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0644"})
        files_api.post.assert_any_call(
            params={"path": "/usr/local/sbin/policy-rc.d"},
            data=policy_rc_d.encode("UTF-8"),
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0755"})
        files_api.session.get.assert_any_call(
            "/1.0/containers/lp-xenial-amd64/files",
            params={"path": "/etc/init/mounted-dev.conf"}, stream=True)
        self.assertNotIn(
            "/etc/init/mounted-dev.override",
            [kwargs["params"]["path"]
             for _, kwargs in files_api.post.call_args_list])
        files_api.post.assert_any_call(
            params={"path": "/etc/systemd/system/snapd.service.d/no-cdn.conf"},
            data=b"[Service]\nEnvironment=SNAPPY_STORE_NO_CDN=1\n",
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0644"})
        container.start.assert_called_once_with(wait=True)
        self.assertEqual(LXD_RUNNING, container.status_code)

    def test_start_missing_etc_hosts(self):
        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/sys")
        fs_fixture.add("/run")
        os.makedirs("/run/launchpad-buildd")
        fs_fixture.add("/etc")
        os.mkdir("/etc")
        with open("/etc/resolv.conf", "w") as f:
            print("host resolv.conf", file=f)
        os.chmod("/etc/resolv.conf", 0o644)
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        client.profiles.get.side_effect = FakeLXDAPIException
        container = client.containers.create.return_value
        client.containers.get.return_value = container
        container.start.side_effect = (
            lambda wait=False: setattr(container, "status_code", LXD_RUNNING))
        files_api = container.api.files
        files_api._api_endpoint = "/1.0/containers/lp-xenial-amd64/files"
        files_api.session.get.side_effect = FakeSessionGet({})
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        processes_fixture.add(lambda _: {}, name="lxc")
        LXD("1", "xenial", "amd64").start()

        files_api.session.get.assert_any_call(
            "/1.0/containers/lp-xenial-amd64/files",
            params={"path": "/etc/hosts"}, stream=True)
        files_api.post.assert_any_call(
            params={"path": "/etc/hosts"},
            data=(
                fallback_hosts +
                "\n127.0.1.1\tlp-xenial-amd64\n").encode("UTF-8"),
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0644"})

    def test_start_with_mounted_dev_conf(self):
        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/sys")
        fs_fixture.add("/run")
        os.makedirs("/run/launchpad-buildd")
        fs_fixture.add("/etc")
        os.mkdir("/etc")
        with open("/etc/resolv.conf", "w") as f:
            print("host resolv.conf", file=f)
        os.chmod("/etc/resolv.conf", 0o644)
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        client.profiles.get.side_effect = FakeLXDAPIException
        container = client.containers.create.return_value
        client.containers.get.return_value = container
        container.start.side_effect = (
            lambda wait=False: setattr(container, "status_code", LXD_RUNNING))
        files_api = container.api.files
        files_api._api_endpoint = "/1.0/containers/lp-trusty-amd64/files"
        files_api.session.get.side_effect = FakeSessionGet({
            "/etc/init/mounted-dev.conf": dedent("""\
                start on mounted MOUNTPOINT=/dev
                script
                    [ -e /dev/shm ] || ln -s /run/shm /dev/shm
                    /sbin/MAKEDEV std fd ppp tun
                end script
                task
                """)})
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        processes_fixture.add(lambda _: {}, name="lxc")
        LXD("1", "trusty", "amd64").start()

        files_api.session.get.assert_any_call(
            "/1.0/containers/lp-trusty-amd64/files",
            params={"path": "/etc/init/mounted-dev.conf"}, stream=True)
        files_api.post.assert_any_call(
            params={"path": "/etc/init/mounted-dev.override"},
            data=dedent("""\
                script
                    [ -e /dev/shm ] || ln -s /run/shm /dev/shm
                    : # /sbin/MAKEDEV std fd ppp tun
                end script
                """).encode("UTF-8"),
            headers={"X-LXD-uid": 0, "X-LXD-gid": 0, "X-LXD-mode": "0644"})

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

    def test_copy_in_error(self):
        source_dir = self.useFixture(TempDir()).path
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        container = mock.MagicMock()
        client.containers.get.return_value = container
        container.api.files.post.side_effect = FakeLXDAPIException
        source_path = os.path.join(source_dir, "source")
        with open(source_path, "w"):
            pass
        target_path = "/path/to/target"
        e = self.assertRaises(
            LXDException, LXD("1", "xenial", "amd64").copy_in,
            source_path, target_path)
        self.assertEqual(
            "Failed to push lp-xenial-amd64:%s: "
            "Fake LXD exception" % target_path, str(e))

    def test_copy_out(self):
        target_dir = self.useFixture(TempDir()).path
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        container = mock.MagicMock()
        client.containers.get.return_value = container
        source_path = "/path/to/source"
        target_path = os.path.join(target_dir, "target")
        files_api = container.api.files
        files_api._api_endpoint = "/1.0/containers/lp-xenial-amd64/files"
        files_api.session.get.side_effect = FakeSessionGet({
            source_path: [b"hello\n", b"world\n"],
            })
        LXD("1", "xenial", "amd64").copy_out(source_path, target_path)

        client.containers.get.assert_called_once_with("lp-xenial-amd64")
        files_api.session.get.assert_called_once_with(
            "/1.0/containers/lp-xenial-amd64/files",
            params={"path": source_path}, stream=True)
        self.assertThat(target_path, FileContains("hello\nworld\n"))

    def test_copy_out_error(self):
        target_dir = self.useFixture(TempDir()).path
        self.useFixture(MockPatch("pylxd.Client"))
        client = pylxd.Client()
        container = mock.MagicMock()
        client.containers.get.return_value = container
        source_path = "/path/to/source"
        target_path = os.path.join(target_dir, "target")
        files_api = container.api.files
        files_api._api_endpoint = "/1.0/containers/lp-xenial-amd64/files"
        files_api.session.get.side_effect = FakeSessionGet({})
        e = self.assertRaises(
            LXDException, LXD("1", "xenial", "amd64").copy_out,
            source_path, target_path)
        self.assertEqual(
            "Failed to pull lp-xenial-amd64:%s: not found" % source_path,
            str(e))

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

    def test_isdir(self):
        processes_fixture = self.useFixture(FakeProcesses())
        test_proc_infos = iter([{}, {"returncode": 1}])
        processes_fixture.add(lambda _: next(test_proc_infos), name="lxc")
        self.assertTrue(LXD("1", "xenial", "amd64").isdir("/dir"))
        self.assertFalse(LXD("1", "xenial", "amd64").isdir("/file"))

        expected_args = [
            ["lxc", "exec", "lp-xenial-amd64", "--",
             "linux64", "test", "-d", path]
            for path in ("/dir", "/file")
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

    def test_find(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        test_proc_infos = iter([
            {"stdout": io.BytesIO(b"foo\0bar\0bar/bar\0bar/baz\0")},
            {"stdout": io.BytesIO(b"foo\0bar\0")},
            {"stdout": io.BytesIO(b"foo\0bar/bar\0bar/baz\0")},
            {"stdout": io.BytesIO(b"bar\0bar/bar\0")},
            ])
        processes_fixture.add(lambda _: next(test_proc_infos), name="lxc")
        self.assertEqual(
            ["foo", "bar", "bar/bar", "bar/baz"],
            LXD("1", "xenial", "amd64").find("/path"))
        self.assertEqual(
            ["foo", "bar"],
            LXD("1", "xenial", "amd64").find("/path", max_depth=1))
        self.assertEqual(
            ["foo", "bar/bar", "bar/baz"],
            LXD("1", "xenial", "amd64").find(
                "/path", include_directories=False))
        self.assertEqual(
            ["bar", "bar/bar"],
            LXD("1", "xenial", "amd64").find("/path", name="bar"))

        find_prefix = [
            "lxc", "exec", "lp-xenial-amd64", "--",
            "linux64", "find", "/path", "-mindepth", "1",
            ]
        find_suffix = ["-printf", "%P\\0"]
        expected_args = [
            find_prefix + find_suffix,
            find_prefix + ["-maxdepth", "1"] + find_suffix,
            find_prefix + ["!", "-type", "d"] + find_suffix,
            find_prefix + ["-name", "bar"] + find_suffix,
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

    def test_is_package_available(self):
        processes_fixture = self.useFixture(FakeProcesses())
        test_proc_infos = iter([
            {"stdout": io.BytesIO(b"Package: snapd\n")},
            {"returncode": 100},
            {"stderr": io.BytesIO(b"N: No packages found\n")},
            ])
        processes_fixture.add(lambda _: next(test_proc_infos), name="lxc")
        self.assertTrue(
            LXD("1", "xenial", "amd64").is_package_available("snapd"))
        self.assertFalse(
            LXD("1", "xenial", "amd64").is_package_available("nonexistent"))
        self.assertFalse(
            LXD("1", "xenial", "amd64").is_package_available("virtual"))

        expected_args = [
            ["lxc", "exec", "lp-xenial-amd64", "--",
             "linux64", "apt-cache", "show", package]
            for package in ("snapd", "nonexistent", "virtual")
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
                    ["-t", "mangle", "-D", "FORWARD", "-i", "lpbuilddbr0",
                     "-p", "tcp", "--tcp-flags", "SYN,RST", "SYN",
                     "-j", "TCPMSS", "--clamp-mss-to-pmtu"] +
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
