# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import sys
import time

from fixtures import (
    EnvironmentVariable,
    MockPatch,
    )
from systemfixtures import (
    FakeProcesses,
    FakeTime,
    )
from testtools import TestCase

from lpbuildd.target.chroot import ChrootUpdater


class TestChrootUpdater(TestCase):

    def test_chroot(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="/usr/bin/sudo")
        ChrootUpdater("1", "xenial", "amd64").chroot(
            ["apt-get", "update"], env={"LANG": "C"})

        expected_args = [
            ["/usr/bin/sudo", "/usr/sbin/chroot",
             "/expected/home/build-1/chroot-autobuild",
             "linux64", "env", "LANG=C", "apt-get", "update"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_update_succeeds(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="/usr/bin/sudo")
        self.useFixture(FakeTime())
        start_time = time.time()
        ChrootUpdater("1", "xenial", "amd64").update()

        apt_get_args = [
            "/usr/bin/sudo", "/usr/sbin/chroot",
            "/expected/home/build-1/chroot-autobuild",
            "linux64", "env",
            "LANG=C",
            "DEBIAN_FRONTEND=noninteractive",
            "TTY=unknown",
            "/usr/bin/apt-get",
            ]
        expected_args = [
            apt_get_args + ["-uy", "update"],
            apt_get_args + [
                "-o", "DPkg::Options::=--force-confold", "-uy", "--purge",
                "dist-upgrade",
                ],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])
        self.assertEqual(start_time, time.time())

    def test_update_first_run_fails(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        apt_get_proc_infos = iter([{"returncode": 1}, {}, {}])
        processes_fixture.add(
            lambda _: next(apt_get_proc_infos), name="/usr/bin/sudo")
        mock_print = self.useFixture(MockPatch("__builtin__.print")).mock
        self.useFixture(FakeTime())
        start_time = time.time()
        ChrootUpdater("1", "xenial", "amd64").update()

        apt_get_args = [
            "/usr/bin/sudo", "/usr/sbin/chroot",
            "/expected/home/build-1/chroot-autobuild",
            "linux64", "env",
            "LANG=C",
            "DEBIAN_FRONTEND=noninteractive",
            "TTY=unknown",
            "/usr/bin/apt-get",
            ]
        expected_args = [
            apt_get_args + ["-uy", "update"],
            apt_get_args + ["-uy", "update"],
            apt_get_args + [
                "-o", "DPkg::Options::=--force-confold", "-uy", "--purge",
                "dist-upgrade",
                ],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])
        mock_print.assert_called_once_with(
            "Waiting 15 seconds and trying again ...", file=sys.stderr)
        self.assertEqual(start_time + 15, time.time())
