# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

from testtools import TestCase

from lpbuildd.target.start import Start


class TestStart(TestCase):

    def test_succeeds(self):
        args = ["--backend=fake", "--series=xenial", "--arch=amd64", "1"]
        start = Start(args=args)
        self.assertEqual(0, start.run())
        self.assertEqual([((), {})], start.backend.start.calls)
