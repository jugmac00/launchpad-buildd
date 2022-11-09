# Copyright 2022 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).
from unittest.mock import patch, ANY

from testtools import TestCase
from fixtures import TempDir

from lpbuildd.tests.fakebuilder import UncontainedBackend


class TestBackend(TestCase):

    def test_open(self):
        backend = UncontainedBackend("1")
        backend_root = self.useFixture(TempDir())
        target_path = backend_root.join("test.txt")

        with patch.object(
            backend, "copy_in", wraps=backend.copy_in
        ) as copy_in, patch.object(
            backend, "copy_out", wraps=backend.copy_out
        ) as copy_out:

            with backend.open(target_path, "w") as f:
                f.write("text")

            copy_out.assert_not_called()
            copy_in.assert_called_once_with(ANY, target_path)

            self.assertTrue(backend.path_exists(target_path))

            copy_in.reset_mock()
            copy_out.reset_mock()

            with backend.open(target_path, "r") as f:
                self.assertEqual(f.read(), "text")

            copy_in.assert_not_called()
            copy_out.assert_called_once_with(target_path, ANY)
