# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import sys
import time
from textwrap import dedent

from fixtures import (
    EnvironmentVariable,
    MockPatch,
    MockPatchObject,
    )
from systemfixtures import (
    FakeProcesses,
    FakeTime,
    )
from testtools import TestCase

from lpbuildd.target.chroot import ChrootSetup
from lpbuildd.tests.fakeslave import FakeMethod


class MockInsertFile(FakeMethod):

    def __init__(self, *args, **kwargs):
        super(MockInsertFile, self).__init__(*args, **kwargs)
        self.source_bytes = None

    def __call__(self, source_path, *args, **kwargs):
        with open(source_path, "rb") as source:
            self.source_bytes = source.read()
        return super(MockInsertFile, self).__call__(
            source_path, *args, **kwargs)


class TestChrootSetup(TestCase):

    def test_chroot(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="/usr/bin/sudo")
        ChrootSetup("1", "xenial", "amd64").chroot(
            ["apt-get", "update"], env={"LANG": "C"})

        expected_args = [
            ["/usr/bin/sudo", "/usr/sbin/chroot",
             "/expected/home/build-1/chroot-autobuild",
             "linux64", "env", "LANG=C", "apt-get", "update"],
            ]
        self.assertEqual(
            expected_args,
            [proc._args["args"] for proc in processes_fixture.procs])

    def test_insert_file(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="/usr/bin/sudo")
        source_path = "/path/to/source"
        target_path = "/path/to/target"
        ChrootSetup("1", "xenial", "amd64").insert_file(
            source_path, target_path)

        expected_target_path = (
            "/expected/home/build-1/chroot-autobuild/path/to/target")
        expected_args = [
            ["/usr/bin/sudo", "install",
             "-o", "root", "-g", "root", "-m", "644",
             source_path, expected_target_path],
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
        ChrootSetup("1", "xenial", "amd64").update()

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
        ChrootSetup("1", "xenial", "amd64").update()

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

    def test_override_sources_list(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        setup = ChrootSetup("1")
        mock_insert_file = self.useFixture(
            MockPatchObject(setup, "insert_file", new=MockInsertFile())).mock
        setup.override_sources_list([
            "deb http://archive.ubuntu.com/ubuntu xenial main",
            "deb http://ppa.launchpad.net/launchpad/ppa/ubuntu xenial main",
            ])

        self.assertEqual(dedent("""\
            deb http://archive.ubuntu.com/ubuntu xenial main
            deb http://ppa.launchpad.net/launchpad/ppa/ubuntu xenial main
            """).encode("UTF-8"), mock_insert_file.source_bytes)
        self.assertEqual(
            ("/etc/apt/sources.list",),
            mock_insert_file.extract_args()[0][1:])
