# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import io

from fixtures import (
    EnvironmentVariable,
    MockPatchObject,
    )
from testtools import TestCase

from lpbuildd.target.add_trusted_keys import AddTrustedKeys


class TestAddTrustedKeys(TestCase):

    def test_add_trusted_keys(self):
        self.useFixture(EnvironmentVariable("HOME", "/expected/home"))
        args = ["--backend=chroot", "--series=xenial", "--arch=amd64", "1"]
        input_file = io.BytesIO()
        add_trusted_keys = AddTrustedKeys(args=args, input_file=input_file)
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
