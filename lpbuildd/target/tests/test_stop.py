# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os.path

from fixtures import EnvironmentVariable
from systemfixtures import (
    FakeFilesystem,
    FakeProcesses,
    )
from testtools import TestCase

from lpbuildd.target.stop import Stop
from lpbuildd.target.tests.testfixtures import SudoUmount


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
        args = ["--backend=chroot", "--series=xenial", "--arch=amd64", "1"]
        Stop(args=args).run()

        # Tested in more detail in lpbuildd.target.tests.test_chroot.
        self.assertIn(
            ["sudo", "umount", "/expected/home/build-1/chroot-autobuild/proc"],
            [proc._args["args"] for proc in processes_fixture.procs])
