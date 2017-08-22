# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

from textwrap import dedent

from fixtures import (
    EnvironmentVariable,
    MockPatchObject,
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
