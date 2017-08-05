# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

from testtools import TestCase

from lpbuildd.target.kill_processes import KillProcesses


class TestKillProcesses(TestCase):

    def test_succeeds(self):
        args = ["--backend=fake", "--series=xenial", "--arch=amd64", "1"]
        kill_processes = KillProcesses(args=args)
        self.assertEqual(0, kill_processes.run())
        self.assertEqual(
            [((), {})], kill_processes.backend.kill_processes.calls)
