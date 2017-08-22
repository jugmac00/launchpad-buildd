# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import io
from textwrap import dedent
import time

from fixtures import (
    EnvironmentVariable,
    FakeLogger,
    MockPatchObject,
    )
from systemfixtures import (
    FakeProcesses,
    FakeTime,
    )
from testtools import TestCase

from lpbuildd.target.cli import parse_args
from lpbuildd.tests.fakeslave import FakeMethod


class MockCopyIn(FakeMethod):

    def __init__(self, *args, **kwargs):
        super(MockCopyIn, self).__init__(*args, **kwargs)
        self.source_bytes = None

    def __call__(self, source_path, *args, **kwargs):
        with open(source_path, "rb") as source:
            self.source_bytes = source.read()
        return super(MockCopyIn, self).__call__(source_path, *args, **kwargs)


class TestOverrideSourcesList(TestCase):

    def test_succeeds(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        args = [
            "override-sources-list",
            "--backend=chroot", "--series=xenial", "--arch=amd64", "1",
            "deb http://archive.ubuntu.com/ubuntu xenial main",
            "deb http://ppa.launchpad.net/launchpad/ppa/ubuntu xenial main",
            ]
        override_sources_list = parse_args(args=args).operation
        mock_copy_in = self.useFixture(MockPatchObject(
            override_sources_list.backend, "copy_in", new=MockCopyIn())).mock
        override_sources_list.run()

        self.assertEqual(dedent("""\
            deb http://archive.ubuntu.com/ubuntu xenial main
            deb http://ppa.launchpad.net/launchpad/ppa/ubuntu xenial main
            """).encode("UTF-8"), mock_copy_in.source_bytes)
        self.assertEqual(
            ("/etc/apt/sources.list",), mock_copy_in.extract_args()[0][1:])


class TestAddTrustedKeys(TestCase):

    def test_add_trusted_keys(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        args = [
            "add-trusted-keys",
            "--backend=chroot", "--series=xenial", "--arch=amd64", "1",
            ]
        input_file = io.BytesIO()
        add_trusted_keys = parse_args(args=args).operation
        add_trusted_keys.input_file = input_file
        # XXX cjwatson 2017-07-29: With a newer version of fixtures we could
        # mock this at the subprocess level instead, but at the moment doing
        # that wouldn't allow us to test stdin.
        mock_backend_run = self.useFixture(
            MockPatchObject(add_trusted_keys.backend, "run")).mock
        add_trusted_keys.run()

        self.assertEqual(2, len(mock_backend_run.mock_calls))
        mock_backend_run.assert_has_calls([
            ((["apt-key", "add", "-"],), {"stdin": input_file}),
            ((["apt-key", "list"],), {}),
            ])


class TestUpdate(TestCase):

    def test_succeeds(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        processes_fixture = self.useFixture(FakeProcesses())
        processes_fixture.add(lambda _: {}, name="sudo")
        self.useFixture(FakeTime())
        start_time = time.time()
        args = [
            "update-debian-chroot",
            "--backend=chroot", "--series=xenial", "--arch=amd64", "1",
            ]
        parse_args(args=args).operation.run()

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
        args = [
            "update-debian-chroot",
            "--backend=chroot", "--series=xenial", "--arch=amd64", "1",
            ]
        parse_args(args=args).operation.run()

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
