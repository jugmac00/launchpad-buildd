# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

from collections import OrderedDict
import logging
import os.path
import subprocess


logger = logging.getLogger(__name__)


class VCSOperationMixin:
    """Methods supporting operations that check out a branch from a VCS."""

    @classmethod
    def add_arguments(cls, parser):
        super(VCSOperationMixin, cls).add_arguments(parser)
        build_from_group = parser.add_mutually_exclusive_group(required=True)
        build_from_group.add_argument(
            "--branch", metavar="BRANCH", help="build from this Bazaar branch")
        build_from_group.add_argument(
            "--git-repository", metavar="REPOSITORY",
            help="build from this Git repository")
        parser.add_argument(
            "--git-path", metavar="REF-PATH",
            help="build from this ref path in REPOSITORY")

    def __init__(self, args, parser):
        super(VCSOperationMixin, self).__init__(args, parser)
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

    def vcs_fetch(self, name, cwd, env=None, quiet=False):
        full_env = OrderedDict()
        full_env["LANG"] = "C.UTF-8"
        full_env["SHELL"] = "/bin/sh"
        if env:
            full_env.update(env)
        if self.args.branch is not None:
            cmd = ["bzr", "branch"]
            if quiet:
                cmd.append("-q")
            cmd.extend([self.args.branch, name])
            if not self.ssl_verify:
                cmd.insert(1, "-Ossl.cert_reqs=none")
        else:
            assert self.args.git_repository is not None
            cmd = ["git", "clone"]
            if quiet:
                cmd.append("-q")
            if self.args.git_path is not None:
                git_path = self.args.git_path
                # "git clone -b" is a bit odd: it takes either branches or
                # tags, but they must be in their short form, i.e. "master"
                # rather than "refs/heads/master" and "1.0" rather than
                # "refs/tags/1.0".  There's thus room for ambiguity if a
                # repository has a branch and a tag with the same name (the
                # branch will win), but using tags in the first place is
                # pretty rare here and a name collision is rarer still.
                # Launchpad shortens branch names before sending them to us,
                # but not tag names.
                if git_path.startswith("refs/tags/"):
                    git_path = git_path[len("refs/tags/"):]
                cmd.extend(["-b", git_path])
            cmd.extend([self.args.git_repository, name])
            if not self.ssl_verify:
                env["GIT_SSL_NO_VERIFY"] = "1"
        self.backend.run(cmd, cwd=cwd, env=full_env)
        if self.args.git_repository is not None:
            try:
                self.backend.run(
                    ["git", "submodule", "update", "--init", "--recursive"],
                    cwd=os.path.join(cwd, name), env=full_env)
            except subprocess.CalledProcessError as e:
                logger.error(
                    "'git submodule update --init --recursive failed with "
                    "exit code %s (build may fail later)" % e.returncode)
