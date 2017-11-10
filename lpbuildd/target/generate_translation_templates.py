# Copyright 2010-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import logging
import os.path

from lpbuildd.pottery import intltool
from lpbuildd.target.operation import Operation


logger = logging.getLogger(__name__)


RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


class GenerateTranslationTemplates(Operation):
    """Script to generate translation templates from a branch."""

    description = "Generate templates for a branch."

    @classmethod
    def add_arguments(cls, parser):
        super(GenerateTranslationTemplates, cls).add_arguments(parser)
        parser.add_argument(
            "branch_spec", help=(
                "A branch URL or the path of a local branch.  URLs are "
                "recognised by the occurrence of ':'.  In the case of a URL, "
                "this will make up a path for the branch and check out the "
                "branch to there."))
        parser.add_argument(
            "result_name",
            help="The name of the result tarball.  Should end in '.tar.gz'.")

    def __init__(self, args, parser):
        super(GenerateTranslationTemplates, self).__init__(args, parser)
        self.work_dir = os.environ["HOME"]

    def _getBranch(self):
        """Set `self.branch_dir`, and check out branch if needed."""
        if ':' in self.args.branch_spec:
            # This is a branch URL.  Check out the branch.
            self.branch_dir = os.path.join(self.work_dir, 'source-tree')
            logger.info("Getting remote branch %s..." % self.args.branch_spec)
            self._checkout(self.args.branch_spec)
        else:
            # This is a local filesystem path.  Use the branch in-place.
            logger.info("Using local branch %s..." % self.args.branch_spec)
            self.branch_dir = self.args.branch_spec

    def _checkout(self, branch_url):
        """Check out a source branch to generate from.

        The branch is checked out to the location specified by
        `self.branch_dir`.
        """
        logger.info(
            "Exporting branch %s to %s..." % (branch_url, self.branch_dir))
        self.backend.run(
            ["bzr", "export", "-q", "-d", branch_url, self.branch_dir])
        logger.info("Exporting branch done.")

    def _makeTarball(self, files):
        """Put the given files into a tarball in the working directory."""
        tarname = os.path.join(self.work_dir, self.args.result_name)
        logger.info("Making tarball with templates in %s..." % tarname)
        cmd = ["tar", "-C", self.branch_dir, "-czf", tarname]
        files = [name for name in files if not name.endswith('/')]
        for path in files:
            full_path = os.path.join(self.branch_dir, path)
            logger.info("Adding template %s..." % full_path)
            cmd.append(path)
        self.backend.run(cmd)
        logger.info("Tarball generated.")

    def run(self):
        """Do It.  Generate templates."""
        try:
            self.backend.run(["apt-get", "-y", "install", "bzr", "intltool"])
        except Exception:
            logger.exception("Install failed")
            return RETCODE_FAILURE_INSTALL
        try:
            logger.info(
                "Generating templates for %s." % self.args.branch_spec)
            self._getBranch()
            pots = intltool.generate_pots(self.backend, self.branch_dir)
            logger.info("Generated %d templates." % len(pots))
            if len(pots) > 0:
                self._makeTarball(pots)
        except Exception:
            logger.exception("Build failed")
            return RETCODE_FAILURE_BUILD
        return 0
