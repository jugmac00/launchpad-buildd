# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os.path
from textwrap import dedent
import time

from fixtures import (
    EnvironmentVariable,
    TempDir,
    )
from systemfixtures import (
    FakeFilesystem,
    FakeProcesses,
    FakeTime,
    )
from testtools import TestCase

from lpbuildd.target.backend import BackendException
from lpbuildd.target.chroot import Chroot
from lpbuildd.target.tests.testfixtures import SudoUmount


class TestChroot(TestCase):

    def test_create(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        Chroot("1", "xenial", "amd64").create("/path/to/tarball")

        expected_args = [
            ["sudo", "tar", "-C", "/expected/home/build-1",
             "-xf", "/path/to/tarball"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_start(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/etc")
        os.mkdir("/etc")
        for etc_name in ("hosts", "hostname", "resolv.conf.real"):
            with open(os.path.join("/etc", etc_name), "w") as etc_file:
                etc_file.write("%s\n" % etc_name)
            os.chmod(os.path.join("/etc", etc_name), 0o644)
        os.symlink("resolv.conf.real", "/etc/resolv.conf")
        Chroot("1", "xenial", "amd64").start()

        expected_args = [
            ["sudo", "mount", "-t", "proc", "none",
             "/expected/home/build-1/chroot-autobuild/proc"],
            ["sudo", "mount", "-t", "devpts", "-o", "gid=5,mode=620", "none",
             "/expected/home/build-1/chroot-autobuild/dev/pts"],
            ["sudo", "mount", "-t", "sysfs", "none",
             "/expected/home/build-1/chroot-autobuild/sys"],
            ["sudo", "mount", "-t", "tmpfs", "none",
             "/expected/home/build-1/chroot-autobuild/dev/shm"],
            ["sudo", "install", "-o", "root", "-g", "root", "-m", "644",
             "/etc/hosts",
             "/expected/home/build-1/chroot-autobuild/etc/hosts"],
            ["sudo", "install", "-o", "root", "-g", "root", "-m", "644",
             "/etc/hostname",
             "/expected/home/build-1/chroot-autobuild/etc/hostname"],
            ["sudo", "install", "-o", "root", "-g", "root", "-m", "644",
             "/etc/resolv.conf",
             "/expected/home/build-1/chroot-autobuild/etc/resolv.conf"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_run(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        Chroot("1", "xenial", "amd64").run(
            ["apt-get", "update"], env={"LANG": "C"})

        expected_args = [
            ["sudo", "/usr/sbin/chroot",
             "/expected/home/build-1/chroot-autobuild",
             "linux64", "env", "LANG=C", "apt-get", "update"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_copy_in(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        source_dir = self.useFixture(TempDir()).path
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        source_path = os.path.join(source_dir, "source")
        with open(source_path, "w"):
            pass
        os.chmod(source_path, 0o644)
        target_path = "/path/to/target"
        Chroot("1", "xenial", "amd64").copy_in(source_path, target_path)

        expected_target_path = (
            "/expected/home/build-1/chroot-autobuild/path/to/target")
        expected_args = [
            ["sudo", "install", "-o", "root", "-g", "root", "-m", "644",
             source_path, expected_target_path],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def _make_initial_proc_mounts(self):
        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/proc")
        os.mkdir("/proc")
        with open("/proc/mounts", "w") as mounts_file:
            mounts_file.write(dedent("""\
                sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0
                proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
                none {chroot}/proc proc rw,relatime 0 0
                none {chroot}/dev/pts devpts rw,relative,gid=5,mode=620 0 0
                none {chroot}/sys sysfs rw,relatime 0 0
                none {chroot}/dev/shm tmpfs rw,relatime 0 0
                """.format(chroot="/expected/home/build-1/chroot-autobuild")))

    def test_stop(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(SudoUmount(), name="sudo")
        self._make_initial_proc_mounts()
        self.useFixture(FakeTime())
        start_time = time.time()
        Chroot("1", "xenial", "amd64").stop()

        expected_chroot_path = "/expected/home/build-1/chroot-autobuild"
        expected_args = [
            ["sudo", "umount", expected_chroot_path + "/dev/shm"],
            ["sudo", "umount", expected_chroot_path + "/sys"],
            ["sudo", "umount", expected_chroot_path + "/dev/pts"],
            ["sudo", "umount", expected_chroot_path + "/proc"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])
        self.assertEqual(start_time, time.time())

    def test_stop_retries(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        delays = {"/expected/home/build-1/chroot-autobuild/sys": 1}
        processes_fixture.add(SudoUmount(delays=delays), name="sudo")
        self._make_initial_proc_mounts()
        self.useFixture(FakeTime())
        start_time = time.time()
        Chroot("1", "xenial", "amd64").stop()

        expected_chroot_path = "/expected/home/build-1/chroot-autobuild"
        expected_args = [
            ["sudo", "umount", expected_chroot_path + "/dev/shm"],
            ["sudo", "umount", expected_chroot_path + "/sys"],
            ["sudo", "umount", expected_chroot_path + "/dev/pts"],
            ["sudo", "umount", expected_chroot_path + "/proc"],
            ["sudo", "umount", expected_chroot_path + "/sys"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])
        self.assertEqual(start_time + 1, time.time())

    def test_stop_too_many_retries(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        delays = {"/expected/home/build-1/chroot-autobuild/sys": 20}
        processes_fixture.add(SudoUmount(delays=delays), name="sudo")
        processes_fixture.add(lambda _: {}, name="lsof")
        self._make_initial_proc_mounts()
        self.useFixture(FakeTime())
        start_time = time.time()
        self.assertRaises(
            BackendException, Chroot("1", "xenial", "amd64").stop)

        expected_chroot_path = "/expected/home/build-1/chroot-autobuild"
        expected_args = [
            ["sudo", "umount", expected_chroot_path + "/dev/shm"],
            ["sudo", "umount", expected_chroot_path + "/sys"],
            ["sudo", "umount", expected_chroot_path + "/dev/pts"],
            ["sudo", "umount", expected_chroot_path + "/proc"],
            ]
        expected_args.extend(
            [["sudo", "umount", expected_chroot_path + "/sys"]] * 19)
        expected_args.append(["lsof", expected_chroot_path])
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])
        self.assertEqual(start_time + 20, time.time())

    def test_remove(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        Chroot("1", "xenial", "amd64").remove()

        expected_args = [["sudo", "rm", "-rf", "/expected/home/build-1"]]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])
