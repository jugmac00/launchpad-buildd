# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

from testtools import TestCase

from lpbuildd.target.remove import Remove


class TestRemove(TestCase):

    def test_succeeds(self):
        args = ["--backend=fake", "--series=xenial", "--arch=amd64", "1"]
        remove = Remove(args=args)
        self.assertEqual(0, remove.run())
        self.assertEqual([((), {})], remove.backend.remove.calls)
