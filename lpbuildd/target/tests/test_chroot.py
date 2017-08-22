# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os.path

from fixtures import (
    EnvironmentVariable,
    TempDir,
    )
from systemfixtures import FakeProcesses
from testtools import TestCase

from lpbuildd.target.chroot import Chroot


class TestChroot(TestCase):

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
