# Copyright 2010-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import os
import subprocess
import tarfile

from fixtures import (
    EnvironmentVariable,
    FakeLogger,
    TempDir,
    )
from testtools import TestCase
from testtools.matchers import (
    ContainsDict,
    EndsWith,
    Equals,
    MatchesListwise,
    MatchesSetwise,
    )

from lpbuildd.target.cli import parse_args
from lpbuildd.tests.fakeslave import FakeMethod


class MatchesCall(MatchesListwise):

    def __init__(self, *args, **kwargs):
        super(MatchesCall, self).__init__([
            Equals(args),
            ContainsDict(
                {name: Equals(value) for name, value in kwargs.items()})])


class TestGenerateTranslationTemplates(TestCase):
    """Test generate-translation-templates script."""

    result_name = "translation-templates.tar.gz"

    def setUp(self):
        super(TestGenerateTranslationTemplates, self).setUp()
        self.home_dir = self.useFixture(TempDir()).path
        self.useFixture(EnvironmentVariable("HOME", self.home_dir))
        self.logger = self.useFixture(FakeLogger())

    def test_getBranch_url(self):
        # If passed a branch URL, the template generation script will
        # check out that branch into a directory called "source-tree."
        args = [
            "generate-translation-templates",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            "lp://~my/translation/branch", self.result_name,
            ]
        generator = parse_args(args=args).operation
        generator._checkout = FakeMethod()
        generator._getBranch()

        self.assertEqual(1, generator._checkout.call_count)
        self.assertThat(generator.branch_dir, EndsWith("source-tree"))

    def test_getBranch_dir(self):
        # If passed a branch directory, the template generation script
        # works directly in that directory.
        branch_dir = "/home/me/branch"
        args = [
            "generate-translation-templates",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            branch_dir, self.result_name,
            ]
        generator = parse_args(args=args).operation
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

        :return: the URL of a fresh bzr branch.
        """
        branch_path = self.useFixture(TempDir()).path
        branch_url = 'file://' + branch_path
        subprocess.check_call(['bzr', 'init', '-q'], cwd=branch_path)

        if content_map is not None:
            for name, contents in content_map.items():
                with open(os.path.join(branch_path, name), 'wb') as f:
                    f.write(contents)
            subprocess.check_call(
                ['bzr', 'add', '-q'] + list(content_map), cwd=branch_path)
            committer_id = 'Committer <committer@example.com>'
            with EnvironmentVariable('BZR_EMAIL', committer_id):
                subprocess.check_call(
                    ['bzr', 'commit', '-q', '-m', 'Populating branch.'],
                    cwd=branch_path)

        return branch_url

    def test_getBranch_bzr(self):
        # _getBranch can retrieve branch contents from a branch URL.
        bzr_home = self.useFixture(TempDir()).path
        self.useFixture(EnvironmentVariable('BZR_HOME', bzr_home))
        self.useFixture(EnvironmentVariable('BZR_EMAIL'))
        self.useFixture(EnvironmentVariable('EMAIL'))

        marker_text = "Ceci n'est pas cet branch."
        branch_url = self._createBranch({'marker.txt': marker_text})

        args = [
            "generate-translation-templates",
            "--backend=uncontained", "--series=xenial", "--arch=amd64", "1",
            branch_url, self.result_name,
            ]
        generator = parse_args(args=args).operation
        generator._getBranch()

        marker_path = os.path.join(generator.branch_dir, 'marker.txt')
        with open(marker_path) as marker_file:
            self.assertEqual(marker_text, marker_file.read())

    def test_templates_tarball(self):
        # Create a tarball from pot files.
        branchdir = os.path.join(self.home_dir, 'branchdir')
        dummy_tar = os.path.join(
            os.path.dirname(__file__), 'dummy_templates.tar.gz')
        with tarfile.open(dummy_tar, 'r|*') as tar:
            tar.extractall(branchdir)
            potnames = [
                member.name
                for member in tar.getmembers() if not member.isdir()]

        args = [
            "generate-translation-templates",
            "--backend=uncontained", "--series=xenial", "--arch=amd64", "1",
            branchdir, self.result_name,
            ]
        generator = parse_args(args=args).operation
        generator._getBranch()
        generator._makeTarball(potnames)
        result_path = os.path.join(self.home_dir, self.result_name)
        with tarfile.open(result_path, 'r|*') as tar:
            tarnames = tar.getnames()
        self.assertThat(tarnames, MatchesSetwise(*(map(Equals, potnames))))

    def test_run(self):
        # Install dependencies and generate a templates tarball.
        branch_url = "lp:~my/branch"
        branch_dir = os.path.join(self.home_dir, "source-tree")
        po_dir = os.path.join(branch_dir, "po")
        result_path = os.path.join(self.home_dir, self.result_name)

        args = [
            "generate-translation-templates",
            "--backend=fake", "--series=xenial", "--arch=amd64", "1",
            branch_url, self.result_name,
            ]
        generator = parse_args(args=args).operation
        generator.backend.add_file(os.path.join(po_dir, "POTFILES.in"), "")
        generator.backend.add_file(
            os.path.join(po_dir, "Makevars"), "DOMAIN = test\n")
        generator.run()
        self.assertThat(generator.backend.run.calls, MatchesListwise([
            MatchesCall(["apt-get", "-y", "install", "bzr", "intltool"]),
            MatchesCall(
                ["bzr", "export", "-q", "-d", "lp:~my/branch", branch_dir]),
            MatchesCall(
                ["rm", "-f",
                 os.path.join(po_dir, "missing"),
                 os.path.join(po_dir, "notexist")]),
            MatchesCall(["/usr/bin/intltool-update", "-m"], cwd=po_dir),
            MatchesCall(
                ["/usr/bin/intltool-update", "-p", "-g", "test"], cwd=po_dir),
            MatchesCall(
                ["tar", "-C", branch_dir, "-czf", result_path, "po/test.pot"]),
            ]))
