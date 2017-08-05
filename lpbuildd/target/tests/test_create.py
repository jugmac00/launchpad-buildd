# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

from testtools import TestCase

from lpbuildd.target.create import Create


class TestCreate(TestCase):

    def test_succeeds(self):
        args = [
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "/path/to/tarball"]
        create = Create(args=args)
        self.assertEqual(0, create.run())
        self.assertEqual(
            [(("/path/to/tarball",), {})], create.backend.create.calls)
