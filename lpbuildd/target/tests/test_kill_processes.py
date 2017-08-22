# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os.path
import signal

from fixtures import EnvironmentVariable
from systemfixtures import FakeFilesystem
from testtools import TestCase
from testtools.matchers import DirContains

from lpbuildd.target.cli import parse_args
from lpbuildd.target.tests.testfixtures import KillFixture


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
