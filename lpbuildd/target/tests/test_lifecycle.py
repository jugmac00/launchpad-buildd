# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os.path
import signal

from fixtures import EnvironmentVariable
from systemfixtures import (
    FakeFilesystem,
    FakeProcesses,
    )
from testtools import TestCase
from testtools.matchers import DirContains

from lpbuildd.target.cli import parse_args
from lpbuildd.target.tests.testfixtures import (
    KillFixture,
    SudoUmount,
    )


class TestCreate(TestCase):

    def test_succeeds(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        args = [
            "unpack-chroot",
            "--backend=chroot", "--series=xenial", "--arch=amd64", "1",
            "/path/to/tarball",
            ]
        parse_args(args=args).operation.run()

        expected_args = [
            ["sudo", "tar", "-C", "/expected/home/build-1",
             "-xf", "/path/to/tarball"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])


class TestStart(TestCase):

    def test_succeeds(self):
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
        args = [
            "mount-chroot",
            "--backend=chroot", "--series=xenial", "--arch=amd64", "1",
            ]
        parse_args(args=args).operation.run()

        # Tested in more detail in lpbuildd.target.tests.test_chroot.
        self.assertIn(
            ["sudo", "mount", "-t", "proc", "none",
            "/expected/home/build-1/chroot-autobuild/proc"],
            [proc._args["args"] for proc in processes_fixture.procs])


class TestKillProcesses(TestCase):

    def test_succeeds(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/expected")
        os.makedirs("/expected/home/build-1/chroot-autobuild")
        fs_fixture.add("/proc")
        os.mkdir("/proc")
        os.mkdir("/proc/10")
        os.symlink("/expected/home/build-1/chroot-autobuild", "/proc/10/root")
        kill_fixture = self.useFixture(KillFixture())
        args = [
            "scan-for-processes",
            "--backend=chroot", "--series=xenial", "--arch=amd64", "1",
            ]
        parse_args(args=args).operation._run()

        self.assertEqual([(10, signal.SIGKILL)], kill_fixture.kills)
        self.assertThat("/proc", DirContains([]))


class TestStop(TestCase):

    def test_succeeds(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(SudoUmount(), name="sudo")
        fs_fixture = self.useFixture(FakeFilesystem())
        fs_fixture.add("/proc")
        os.mkdir("/proc")
        with open("/proc/mounts", "w") as mounts_file:
            mounts_file.write(
                "none {chroot}/proc proc rw,relatime 0 0".format(
                    chroot="/expected/home/build-1/chroot-autobuild"))
        args = [
            "umount-chroot",
            "--backend=chroot", "--series=xenial", "--arch=amd64", "1",
            ]
        parse_args(args=args).operation.run()

        # Tested in more detail in lpbuildd.target.tests.test_chroot.
        self.assertIn(
            ["sudo", "umount", "/expected/home/build-1/chroot-autobuild/proc"],
            [proc._args["args"] for proc in processes_fixture.procs])


class TestRemove(TestCase):

    def test_succeeds(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        args = [
            "remove-build",
            "--backend=chroot", "--series=xenial", "--arch=amd64", "1",
            ]
        parse_args(args=args).operation.run()

        expected_args = [["sudo", "rm", "-rf", "/expected/home/build-1"]]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])
