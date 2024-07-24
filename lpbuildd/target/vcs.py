# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import logging
import os.path
import subprocess
from collections import OrderedDict

from lpbuildd.target.status import StatusOperationMixin

logger = logging.getLogger(__name__)


class VCSOperationMixin(StatusOperationMixin):
    """Methods supporting operations that check out a branch from a VCS."""

    @classmethod
    def add_arguments(cls, parser):
        super().add_arguments(parser)
        build_from_group = parser.add_mutually_exclusive_group(required=True)
        build_from_group.add_argument(
            "--branch", metavar="BRANCH", help="build from this Bazaar branch"
        )
        build_from_group.add_argument(
            "--git-repository",
            metavar="REPOSITORY",
            help="build from this Git repository",
        )
        parser.add_argument(
            "--git-path",
            metavar="REF-PATH",
            help="build from this ref path in REPOSITORY",
        )

    def __init__(self, args, parser):
        super().__init__(args, parser)
        if args.git_repository is None and args.git_path is not None:
            parser.error("--git-path requires --git-repository")
        # Set to False for local testing if your target doesn't have an
        # appropriate certificate for your codehosting system.
        self.ssl_verify = True

    @property
    def vcs_description(self):
        if self.args.branch is not None:
            return self.args.branch
        else:
            assert self.args.git_repository is not None
            description = self.args.git_repository
            if self.args.git_path is not None:
                description += " " + self.args.git_path
            return description

    @property
    def vcs_deps(self):
        if self.args.branch is not None:
            return ["bzr"]
        else:
            return ["git"]

    def vcs_fetch(
        self,
        name,
        cwd,
        env=None,
        quiet=False,
        git_shallow_clone_with_single_branch=False,
    ):
        full_env = OrderedDict()
        full_env["LANG"] = "C.UTF-8"
        full_env["SHELL"] = "/bin/sh"
        if env:
            full_env.update(env)
        # XXX: jugmac00 2024-07-24: this method could be refactored to make it
        # more clear that we both handle the bzr and the git case
        # or even better, we should have separate classes to handle git and bzr

        # this handles the bzr case
        if self.args.branch is not None:
            cmd = ["bzr", "branch"]
            if quiet:
                cmd.append("-q")
            cmd.extend([self.args.branch, name])
            if not self.ssl_verify:
                cmd.insert(1, "-Ossl.cert_reqs=none")
        else:
            # this handles the git case
            assert self.args.git_repository is not None
            cmd = ["git", "clone", "-n"]
            if quiet:
                cmd.append("-q")
            git_path = self.args.git_path
            if self.args.git_path is None:
                git_path = "HEAD"
            if git_shallow_clone_with_single_branch:
                cmd.extend(["--depth", "1", "-b", git_path, "--single-branch"])
            cmd.extend([self.args.git_repository, name])
            if not self.ssl_verify:
                env["GIT_SSL_NO_VERIFY"] = "1"
        self.backend.run(cmd, cwd=cwd, env=full_env)
        # this handles the git case
        if self.args.git_repository is not None:
            repository = os.path.join(cwd, name)
            self.backend.run(
                ["git", "checkout", "-q", git_path],
                cwd=repository,
                env=full_env,
            )
            try:
                self.backend.run(
                    ["git", "submodule", "update", "--init", "--recursive"],
                    cwd=repository,
                    env=full_env,
                )
            except subprocess.CalledProcessError as e:
                logger.error(
                    "'git submodule update --init --recursive failed with "
                    "exit code %s (build may fail later)" % e.returncode
                )

    def vcs_update_status(self, cwd):
        """Update this operation's status with VCS information."""
        if self.args.branch is not None:
            revision_id = self.run_build_command(
                ["bzr", "revno"],
                cwd=cwd,
                get_output=True,
                universal_newlines=True,
            ).rstrip("\n")
        else:
            rev = (
                self.args.git_path
                if self.args.git_path is not None
                else "HEAD"
            )
            revision_id = self.run_build_command(
                # The ^{} suffix copes with tags: we want to peel them
                # recursively until we get an actual commit.
                ["git", "rev-parse", rev + "^{}"],
                cwd=cwd,
                get_output=True,
                universal_newlines=True,
            ).rstrip("\n")
        self.update_status(revision_id=revision_id)
