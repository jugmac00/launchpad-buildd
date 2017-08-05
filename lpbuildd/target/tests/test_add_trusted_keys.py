# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import io

from testtools import TestCase

from lpbuildd.target.add_trusted_keys import AddTrustedKeys


class TestAddTrustedKeys(TestCase):

    def test_add_trusted_keys(self):
        args = ["--backend=fake", "--series=xenial", "--arch=amd64", "1"]
        input_file = io.BytesIO()
        add_trusted_keys = AddTrustedKeys(args=args, input_file=input_file)
        self.assertEqual(0, add_trusted_keys.run())
        expected_run = [
            ((["apt-key", "add", "-"],), {"stdin": input_file}),
            ((["apt-key", "list"],), {}),
            ]
        self.assertEqual(expected_run, add_trusted_keys.backend.run.calls)
