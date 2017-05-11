# Copyright 2010-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os
from StringIO import StringIO
import subprocess
import sys
import tarfile

from bzrlib.bzrdir import BzrDir
from bzrlib.generate_ids import (
    gen_file_id,
    gen_root_id,
    )
from bzrlib.transform import (
    ROOT_PARENT,
    TransformPreview,
    )
from fixtures import (
    EnvironmentVariable,
    TempDir,
    )
from testtools import TestCase
from testtools.matchers import (
    Equals,
    MatchesSetwise,
    )

from lpbuildd import pottery
from lpbuildd.pottery.generate_translation_templates import (
    GenerateTranslationTemplates,
    )
from lpbuildd.tests.fakeslave import FakeMethod


class TestGenerateTranslationTemplates(TestCase):
    """Test generate-translation-templates script."""

    result_name = "translation-templates.tar.gz"

    def test_getBranch_url(self):
        # If passed a branch URL, the template generation script will
        # check out that branch into a directory called "source-tree."
        branch_url = 'lp://~my/translation/branch'

        generator = GenerateTranslationTemplates(
            branch_url, self.result_name, self.useFixture(TempDir()).path,
            log_file=StringIO())
        generator._checkout = FakeMethod()
        generator._getBranch()

        self.assertEqual(1, generator._checkout.call_count)
        self.assertTrue(generator.branch_dir.endswith('source-tree'))

    def test_getBranch_dir(self):
        # If passed a branch directory, the template generation script
        # works directly in that directory.
        branch_dir = '/home/me/branch'

        generator = GenerateTranslationTemplates(
            branch_dir, self.result_name, self.useFixture(TempDir()).path,
            log_file=StringIO())
        generator._checkout = FakeMethod()
        generator._getBranch()

        self.assertEqual(0, generator._checkout.call_count)
        self.assertEqual(branch_dir, generator.branch_dir)

    def _createBranch(self, content_map=None):
        """Create a working branch.

        :param content_map: optional dict mapping file names to file
            contents.  Each of these files with their contents will be
            written to the branch.  Currently only supports writing files at
            the root directory of the branch.

        :return: a tuple of a fresh bzr branch and its URL.
        """
        branch_url = 'file://' + self.useFixture(TempDir()).path
        branch = BzrDir.create_branch_convenience(branch_url)

        if content_map is not None:
            branch.lock_write()
            try:
                revision_tree = branch.basis_tree()
                transform_preview = TransformPreview(revision_tree)
                try:
                    root_id = transform_preview.new_directory(
                        '', ROOT_PARENT, gen_root_id())
                    for name, contents in content_map.iteritems():
                        file_id = gen_file_id(name)
                        transform_preview.new_file(
                            name, root_id, [contents], file_id=file_id)
                    committer_id = 'Committer <committer@example.com>'
                    with EnvironmentVariable('BZR_EMAIL', committer_id):
                        transform_preview.commit(
                            branch, 'Populating branch.',
                            committer=committer_id)
                finally:
                    transform_preview.finalize()
            finally:
                branch.unlock()

        return branch, branch_url

    def test_getBranch_bzr(self):
        # _getBranch can retrieve branch contents from a branch URL.
        bzr_home = self.useFixture(TempDir()).path
        self.useFixture(EnvironmentVariable('BZR_HOME', bzr_home))
        self.useFixture(EnvironmentVariable('BZR_EMAIL'))
        self.useFixture(EnvironmentVariable('EMAIL'))

        marker_text = "Ceci n'est pas cet branch."
        branch, branch_url = self._createBranch({'marker.txt': marker_text})

        generator = GenerateTranslationTemplates(
            branch_url, self.result_name, self.useFixture(TempDir()).path,
            log_file=StringIO())
        generator._getBranch()

        marker_path = os.path.join(generator.branch_dir, 'marker.txt')
        with open(marker_path) as marker_file:
            self.assertEqual(marker_text, marker_file.read())

    def test_templates_tarball(self):
        # Create a tarball from pot files.
        workdir = self.useFixture(TempDir()).path
        branchdir = os.path.join(workdir, 'branchdir')
        dummy_tar = os.path.join(
            os.path.dirname(__file__), 'dummy_templates.tar.gz')
        with tarfile.open(dummy_tar, 'r|*') as tar:
            tar.extractall(branchdir)
            potnames = [
                member.name
                for member in tar.getmembers() if not member.isdir()]

        generator = GenerateTranslationTemplates(
            branchdir, self.result_name, workdir, log_file=StringIO())
        generator._getBranch()
        generator._makeTarball(potnames)
        result_path = os.path.join(workdir, self.result_name)
        with tarfile.open(result_path, 'r|*') as tar:
            tarnames = tar.getnames()
        self.assertThat(tarnames, MatchesSetwise(*(map(Equals, potnames))))

    def test_script(self):
        tempdir = self.useFixture(TempDir()).path
        workdir = self.useFixture(TempDir()).path
        command = [
            sys.executable,
            os.path.join(
                os.path.dirname(pottery.__file__),
                'generate_translation_templates.py'),
            tempdir, self.result_name, workdir]
        retval = subprocess.call(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(0, retval)
