# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import io
import json
import os.path
import tarfile
from textwrap import dedent

from fixtures import (
    EnvironmentVariable,
    MonkeyPatch,
    TempDir,
    )
from systemfixtures import (
    FakeFilesystem,
    FakeProcesses,
    )
from testtools import TestCase
from testtools.matchers import (
    DirContains,
    EndsWith,
    Equals,
    FileContains,
    HasPermissions,
    MatchesDict,
    MatchesListwise,
    )

from lpbuildd.target.lxd import LXD


class TestLXD(TestCase):

    def make_chroot_tarball(self, output_path):
        source = self.useFixture(TempDir()).path
        hello = os.path.join(source, "bin", "hello")
        os.mkdir(os.path.dirname(hello))
        with open(hello, "w") as f:
            f.write("hello\n")
            os.fchmod(f.fileno(), 0o755)
        os.mkdir(os.path.join(source, "etc"))
        for name in ("hosts", "hostname", "resolv.conf"):
            with open(os.path.join(source, "etc", name), "w") as f:
                f.write("%s\n" % name)
        policy_rc_d = os.path.join(
            source, "usr", "local", "sbin", "policy-rc.d")
        os.makedirs(os.path.dirname(policy_rc_d))
        with open(policy_rc_d, "w") as f:
            f.write("original policy-rc.d\n")
            os.fchmod(f.fileno(), 0o755)
        with tarfile.open(output_path, "w:bz2") as tar:
            tar.add(source, arcname="chroot-autobuild")

    def make_fake_etc(self):
        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/etc")
        os.mkdir("/etc")
        for name in ("hosts", "hostname", "resolv.conf"):
            with open(os.path.join("/etc", name), "w") as f:
                f.write("host %s\n" % name)
        # systemfixtures doesn't patch this, but arguably should.
        self.useFixture(MonkeyPatch("tarfile.bltn_open", open))

    def test_convert(self):
        tmp = self.useFixture(TempDir()).path
        source_tarball_path = os.path.join(tmp, "source.tar.bz2")
        target_tarball_path = os.path.join(tmp, "target.tar.gz")
        self.make_chroot_tarball(source_tarball_path)
        self.make_fake_etc()
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
        self.assertThat(rootfs, DirContains(["bin", "etc", "usr"]))
        self.assertThat(os.path.join(rootfs, "bin"), DirContains(["hello"]))
        hello = os.path.join(rootfs, "bin", "hello")
        self.assertThat(hello, FileContains("hello\n"))
        self.assertThat(hello, HasPermissions("0755"))
        self.assertThat(
            os.path.join(rootfs, "etc"),
            DirContains(["hosts", "hostname", "resolv.conf"]))
        for name in ("hosts", "hostname", "resolv.conf"):
            self.assertThat(
                os.path.join(rootfs, "etc", name),
                FileContains("host %s\n" % name))
        policy_rc_d = os.path.join(
            rootfs, "usr", "local", "sbin", "policy-rc.d")
        self.assertThat(
            policy_rc_d,
            FileContains(dedent("""\
                #! /bin/sh
                while :; do
                    case "$1" in
                        -*)             shift ;;
                        snapd.service)  exit 0 ;;
                        *)
                            echo "Not running services in chroot."
                            exit 101
                            ;;
                    esac
                done
                """)))
        self.assertThat(policy_rc_d, HasPermissions("0755"))

    def test_create(self):
        tmp = self.useFixture(TempDir()).path
        source_tarball_path = os.path.join(tmp, "source.tar.bz2")
        self.make_chroot_tarball(source_tarball_path)
        self.make_fake_etc()
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(
            lambda proc_args: {
                "returncode": 1 if "info" in proc_args["args"] else 0,
                },
            name="sudo")
        LXD("1", "xenial", "amd64").create(source_tarball_path)

        self.assertThat(
            [proc._args["args"] for proc in processes_fixture.procs],
            MatchesListwise([
                Equals(["sudo", "lxc", "image", "info", "lp-xenial-amd64"]),
                MatchesListwise([
                    Equals("sudo"), Equals("lxc"), Equals("image"),
                    Equals("import"), EndsWith("/lxd.tar.gz"),
                    Equals("--alias"), Equals("lp-xenial-amd64"),
                    ]),
                ]))

    def test_start(self):
        class SudoLXC:
            def __init__(self):
                self.created = False
                self.started = False

            def __call__(self, proc_info):
                ret = {}
                if proc_info["args"][:4] == ["sudo", "lxc", "profile", "show"]:
                    ret["returncode"] = 1
                elif proc_info["args"][:3] == ["sudo", "lxc", "init"]:
                    self.created = True
                elif proc_info["args"][:3] == ["sudo", "lxc", "start"]:
                    self.started = True
                elif proc_info["args"][:3] == ["sudo", "lxc", "info"]:
                    if not self.created:
                        ret["returncode"] = 1
                    else:
                        status = "Running" if self.started else "Stopped"
                        ret["stdout"] = io.BytesIO(
                            ("Status: %s\n" % status).encode("UTF-8"))
                return ret

        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/sys")
        fs_fixture.add("/run")
        os.makedirs("/run/launchpad-buildd")
        fs_fixture.add("/etc")
        os.mkdir("/etc")
        for name in ("hosts", "hostname", "resolv.conf"):
            with open(os.path.join("/etc", name), "w") as f:
                f.write("host %s\n" % name)
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(SudoLXC(), name="sudo")
        LXD("1", "xenial", "amd64").start()

        lxc = ["sudo", "lxc"]
        raw_lxc = dedent("""\
            lxc.aa_profile=unconfined
            lxc.cgroup.devices.deny=
            lxc.cgroup.devices.allow=
            lxc.network.0.ipv4=10.10.10.2/24
            lxc.network.0.ipv4.gateway=10.10.10.1
            """)
        ip = ["sudo", "ip"]
        iptables = ["sudo", "iptables", "-w"]
        iptables_comment = [
            "-m", "comment", "--comment", "managed by launchpad-buildd"]
        self.assertThat(
            [proc._args["args"] for proc in processes_fixture.procs],
            MatchesListwise([
                Equals(lxc + ["info", "lp-xenial-amd64"]),
                Equals(lxc + ["info", "lp-xenial-amd64"]),
                Equals(lxc + ["profile", "show", "lpbuildd"]),
                Equals(lxc + ["profile", "copy", "default", "lpbuildd"]),
                Equals(lxc + ["profile", "device", "set", "lpbuildd", "eth0",
                              "parent", "lpbr0"]),
                Equals(lxc + ["profile", "set", "lpbuildd",
                              "security.privileged", "true"]),
                Equals(lxc + ["profile", "set", "lpbuildd",
                              "raw.lxc", raw_lxc]),
                Equals(ip + ["link", "add", "dev", "lpbr0", "type", "bridge"]),
                Equals(ip + ["addr", "add", "10.10.10.1/24", "dev", "lpbr0"]),
                Equals(ip + ["link", "set", "dev", "lpbr0", "up"]),
                Equals(
                    ["sudo", "sh", "-c",
                     "echo 1 >/proc/sys/net/ipv4/ip_forward"]),
                Equals(
                    iptables +
                    ["-t", "nat", "-A", "POSTROUTING",
                     "-s", "10.10.10.1/24", "!", "-d", "10.10.10.1/24",
                     "-j", "MASQUERADE"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-I", "INPUT", "-i", "lpbr0",
                     "-p", "udp", "--dport", "53", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-I", "INPUT", "-i", "lpbr0",
                     "-p", "tcp", "--dport", "53", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-I", "FORWARD", "-i", "lpbr0", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-I", "FORWARD", "-o", "lpbr0", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    ["sudo", "/usr/sbin/dnsmasq", "-s", "lpbuildd",
                     "-S", "/lpbuildd/", "-u", "buildd", "--strict-order",
                     "--bind-interfaces",
                     "--pid-file=/run/launchpad-buildd/dnsmasq.pid",
                     "--except-interface=lo", "--interface=lpbr0",
                     "--listen-address=10.10.10.1"]),
                Equals(lxc + ["init", "--ephemeral", "-p", "lpbuildd",
                              "lp-xenial-amd64", "lp-xenial-amd64"]),
                Equals(lxc + ["file", "push",
                              "--uid=0", "--gid=0", "--mode=644",
                              "/etc/hosts", "lp-xenial-amd64/etc/hosts"]),
                Equals(lxc + ["file", "push",
                              "--uid=0", "--gid=0", "--mode=644",
                              "/etc/hostname",
                              "lp-xenial-amd64/etc/hostname"]),
                Equals(lxc + ["file", "push",
                              "--uid=0", "--gid=0", "--mode=644",
                              "/etc/resolv.conf",
                              "lp-xenial-amd64/etc/resolv.conf"]),
                Equals(lxc + ["start", "lp-xenial-amd64"]),
                Equals(lxc + ["info", "lp-xenial-amd64"]),
                ]))

    def test_run(self):
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        LXD("1", "xenial", "amd64").run(
            ["apt-get", "update"], env={"LANG": "C"})

        expected_args = [
            ["sudo", "lxc", "exec", "lp-xenial-amd64", "--",
             "linux64", "env", "LANG=C", "apt-get", "update"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_run_get_output(self):
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(
            lambda _: {"stdout": io.BytesIO(b"hello\n")}, name="sudo")
        self.assertEqual(
            "hello\n",
            LXD("1", "xenial", "amd64").run(
                ["echo", "hello"], get_output=True))

        expected_args = [
            ["sudo", "lxc", "exec", "lp-xenial-amd64", "--",
             "linux64", "echo", "hello"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_copy_in(self):
        source_dir = self.useFixture(TempDir()).path
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        source_path = os.path.join(source_dir, "source")
        with open(source_path, "w"):
            pass
        os.chmod(source_path, 0o644)
        target_path = "/path/to/target"
        LXD("1", "xenial", "amd64").copy_in(source_path, target_path)

        expected_args = [
            ["sudo", "lxc", "file", "push", "--uid=0", "--gid=0", "--mode=644",
             source_path, "lp-xenial-amd64" + target_path],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_copy_out(self):
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        LXD("1", "xenial", "amd64").copy_out(
            "/path/to/source", "/path/to/target")

        expected_args = [
            ["sudo", "lxc", "file", "pull",
             "lp-xenial-amd64/path/to/source", "/path/to/target"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_path_exists(self):
        processes_fixture = self.useFixture(FakeProcesses())
        test_proc_infos = iter([{}, {"returncode": 1}])
        processes_fixture.add(lambda _: next(test_proc_infos), name="sudo")
        self.assertTrue(LXD("1", "xenial", "amd64").path_exists("/present"))
        self.assertFalse(LXD("1", "xenial", "amd64").path_exists("/absent"))

        expected_args = [
            ["sudo", "lxc", "exec", "lp-xenial-amd64", "--",
             "linux64", "test", "-e", path]
            for path in ("/present", "/absent")
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_islink(self):
        processes_fixture = self.useFixture(FakeProcesses())
        test_proc_infos = iter([{}, {"returncode": 1}])
        processes_fixture.add(lambda _: next(test_proc_infos), name="sudo")
        self.assertTrue(LXD("1", "xenial", "amd64").islink("/link"))
        self.assertFalse(LXD("1", "xenial", "amd64").islink("/file"))

        expected_args = [
            ["sudo", "lxc", "exec", "lp-xenial-amd64", "--",
             "linux64", "test", "-h", path]
            for path in ("/link", "/file")
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_listdir(self):
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(
            lambda _: {"stdout": io.BytesIO(b"foo\0bar\0baz\0")}, name="sudo")
        self.assertEqual(
            ["foo", "bar", "baz"],
            LXD("1", "xenial", "amd64").listdir("/path"))

        expected_args = [
            ["sudo", "lxc", "exec", "lp-xenial-amd64", "--",
             "linux64", "find", "/path", "-mindepth", "1", "-maxdepth", "1",
             "-printf", "%P\\0"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_stop(self):
        class SudoLXC:
            def __init__(self):
                self.stopped = False
                self.deleted = False

            def __call__(self, proc_info):
                ret = {}
                if proc_info["args"][:3] == ["sudo", "lxc", "stop"]:
                    self.stopped = True
                elif proc_info["args"][:3] == ["sudo", "lxc", "delete"]:
                    self.deleted = True
                elif proc_info["args"][:3] == ["sudo", "lxc", "info"]:
                    if self.deleted:
                        ret["returncode"] = 1
                    else:
                        status = "Stopped" if self.stopped else "Running"
                        ret["stdout"] = io.BytesIO(
                            ("Status: %s\n" % status).encode("UTF-8"))
                return ret

        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/sys")
        os.makedirs("/sys/class/net/lpbr0")
        fs_fixture.add("/run")
        os.makedirs("/run/launchpad-buildd")
        with open("/run/launchpad-buildd/dnsmasq.pid", "w") as f:
            f.write("42\n")
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(SudoLXC(), name="sudo")
        LXD("1", "xenial", "amd64").stop()

        lxc = ["sudo", "lxc"]
        ip = ["sudo", "ip"]
        iptables = ["sudo", "iptables", "-w"]
        iptables_comment = [
            "-m", "comment", "--comment", "managed by launchpad-buildd"]
        self.assertThat(
            [proc._args["args"] for proc in processes_fixture.procs],
            MatchesListwise([
                Equals(lxc + ["info", "lp-xenial-amd64"]),
                Equals(lxc + ["stop", "lp-xenial-amd64"]),
                Equals(lxc + ["info", "lp-xenial-amd64"]),
                Equals(lxc + ["delete", "lp-xenial-amd64"]),
                Equals(ip + ["addr", "flush", "dev", "lpbr0"]),
                Equals(ip + ["link", "set", "dev", "lpbr0", "down"]),
                Equals(
                    iptables +
                    ["-D", "INPUT", "-i", "lpbr0",
                     "-p", "udp", "--dport", "53", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-D", "INPUT", "-i", "lpbr0",
                     "-p", "tcp", "--dport", "53", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-D", "FORWARD", "-i", "lpbr0", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-D", "FORWARD", "-o", "lpbr0", "-j", "ACCEPT"] +
                    iptables_comment),
                Equals(
                    iptables +
                    ["-t", "nat", "-D", "POSTROUTING",
                     "-s", "10.10.10.1/24", "!", "-d", "10.10.10.1/24",
                     "-j", "MASQUERADE"] +
                    iptables_comment),
                Equals(["sudo", "kill", "-9", "42"]),
                Equals(ip + ["link", "delete", "lpbr0"]),
                ]))

    def test_remove(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        LXD("1", "xenial", "amd64").remove()

        lxc = ["sudo", "lxc"]
        self.assertThat(
            [proc._args["args"] for proc in processes_fixture.procs],
            MatchesListwise([
                Equals(lxc + ["image", "info", "lp-xenial-amd64"]),
                Equals(lxc + ["image", "delete", "lp-xenial-amd64"]),
                Equals(["sudo", "rm", "-rf", "/expected/home/build-1"]),
                ]))
