# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import time

from fixtures import (
    EnvironmentVariable,
    FakeLogger,
    )
from systemfixtures import (
    FakeProcesses,
    FakeTime,
    )
from testtools import TestCase

from lpbuildd.target.update import Update


class TestUpdate(TestCase):

    def test_succeeds(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        self.useFixture(FakeTime())
        start_time = time.time()
        args = ["--backend=chroot", "--series=xenial", "--arch=amd64", "1"]
        Update(args=args).run()

        apt_get_args = [
            "sudo", "/usr/sbin/chroot",
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

    def test_first_run_fails(self):
        logger = self.useFixture(FakeLogger())
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        apt_get_proc_infos = iter([{"returncode": 1}, {}, {}])
        processes_fixture.add(lambda _: next(apt_get_proc_infos), name="sudo")
        self.useFixture(FakeTime())
        start_time = time.time()
        args = ["--backend=chroot", "--series=xenial", "--arch=amd64", "1"]
        Update(args=args).run()

        apt_get_args = [
            "sudo", "/usr/sbin/chroot",
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
        self.assertEqual(
            "Updating target for build 1\n"
            "Waiting 15 seconds and trying again ...\n",
            logger.output)
        self.assertEqual(start_time + 15, time.time())
