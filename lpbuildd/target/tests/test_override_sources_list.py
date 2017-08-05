# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

from textwrap import dedent

from testtools import TestCase

from lpbuildd.target.override_sources_list import OverrideSourcesList


class TestOverrideSourcesList(TestCase):

    def test_succeeds(self):
        args = [
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "deb http://archive.ubuntu.com/ubuntu xenial main",
            "deb http://ppa.launchpad.net/launchpad/ppa/ubuntu xenial main",
            ]
        override_sources_list = OverrideSourcesList(args=args)
        self.assertEqual(0, override_sources_list.run())
        self.assertEqual({
            "/etc/apt/sources.list": dedent("""\
                deb http://archive.ubuntu.com/ubuntu xenial main
                deb http://ppa.launchpad.net/launchpad/ppa/ubuntu xenial main
                """).encode("UTF-8"),
            }, override_sources_list.backend.copied_in)
