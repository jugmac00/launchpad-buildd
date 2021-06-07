# Copyright 2021 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function
import functools

__metaclass__ = type

from collections import OrderedDict
import logging
import os
import sys

from lpbuildd.target.backend import InvalidBuildFilePath
from lpbuildd.target.operation import Operation
from lpbuildd.target.vcs import VCSOperationMixin


RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


logger = logging.getLogger(__name__)


class BuildCharm(VCSOperationMixin, Operation):

    description = "Build a charm."

    # charmcraft is a snap, so we'll need these
    core_snap_names = ["core", "core20"]

    @classmethod
    def add_arguments(cls, parser):
        super(BuildCharm, cls).add_arguments(parser)
        parser.add_argument(
            "--build-path", default=".",
            help="location of charm to build.")
        parser.add_argument("name", help="name of charm to build")

    def __init__(self, args, parser):
        super(BuildCharm, self).__init__(args, parser)
        self.bin = os.path.dirname(sys.argv[0])
        self.buildd_path = os.path.join("/home/buildd", self.args.name)

    def _check_path_escape(self, path_to_check):
        """Check the build file path doesn't escape the build directory."""
        build_file_path = os.path.realpath(
            os.path.join(self.buildd_path, path_to_check))
        common_path = os.path.commonprefix((build_file_path, self.buildd_path))
        if common_path != self.buildd_path:
            raise InvalidBuildFilePath("Invalid build file path.")

    def run_build_command(self, args, env=None, build_path=None, **kwargs):
        """Run a build command in the target.

        :param args: the command and arguments to run.
        :param env: dictionary of additional environment variables to set.
        :param kwargs: any other keyword arguments to pass to Backend.run.
        """
        full_env = OrderedDict()
        full_env["LANG"] = "C.UTF-8"
        full_env["SHELL"] = "/bin/sh"
        if env:
            full_env.update(env)
        return self.backend.run(
            args, cwd=self.buildd_path, env=full_env, **kwargs)

    def install(self):
        logger.info("Running install phase")
        deps = []
        if self.args.backend == "lxd":
            # udev is installed explicitly to work around
            # https://bugs.launchpad.net/snapd/+bug/1731519.
            for dep in "snapd", "fuse", "squashfuse", "udev":
                if self.backend.is_package_available(dep):
                    deps.append(dep)
        deps.extend(self.vcs_deps)
        self.backend.run(["apt-get", "-y", "install"] + deps)
        for snap_name in self.core_snap_names:
            self.backend.run(["snap", "install", snap_name])
        self.backend.run(
            ["snap", "install", "charmcraft"])
        # The charmcraft snap can't see /build, so we have to do our work under
        # /home/buildd instead.  Make sure it exists.
        self.backend.run(["mkdir", "-p", "/home/buildd"])

    def repo(self):
        """Collect git or bzr branch."""
        logger.info("Running repo phase...")
        self.vcs_fetch(self.args.name, cwd="/home/buildd")

    def build(self):
        logger.info("Running build phase...")
        build_context_path = os.path.join(
            "/home/buildd",
            self.args.name,
            self.args.build_path)
        self._check_path_escape(build_context_path)
        args = ["charmcraft", "build", "-f", build_context_path]
        self.run_build_command(args)

    def run(self):
        try:
            self.install()
        except Exception:
            logger.exception('Install failed')
            return RETCODE_FAILURE_INSTALL
        try:
            self.repo()
            self.build()
        except Exception:
            logger.exception('Build failed')
            return RETCODE_FAILURE_BUILD
        return 0

