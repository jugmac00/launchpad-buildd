# Copyright 2021 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function
import functools

__metaclass__ = type

from collections import OrderedDict
import logging
import os
import sys

from lpbuildd.target.backend import check_path_escape
from lpbuildd.target.build_snap import SnapChannelsAction
from lpbuildd.target.operation import Operation
from lpbuildd.target.snapstore import SnapStoreOperationMixin
from lpbuildd.target.vcs import VCSOperationMixin


RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


logger = logging.getLogger(__name__)


class BuildCharm(VCSOperationMixin, SnapStoreOperationMixin, Operation):

    description = "Build a charm."

    core_snap_names = ["core", "core16", "core18", "core20"]

    @classmethod
    def add_arguments(cls, parser):
        super(BuildCharm, cls).add_arguments(parser)
        parser.add_argument(
            "--channel", action=SnapChannelsAction, metavar="SNAP=CHANNEL",
            dest="channels", default={}, help=(
                "install SNAP from CHANNEL "
                "(supported snaps: {}, charmcraft)".format(
                    ", ".join(cls.core_snap_names))))
        parser.add_argument(
            "--build-path", default=".",
            help="location of charm to build.")
        parser.add_argument("name", help="name of charm to build")

    def __init__(self, args, parser):
        super(BuildCharm, self).__init__(args, parser)
        self.bin = os.path.dirname(sys.argv[0])
        self.buildd_path = os.path.join("/home/buildd", self.args.name)

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
        cwd = kwargs.pop('cwd', self.buildd_path)
        return self.backend.run(
            args, cwd=cwd, env=full_env, **kwargs)

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
        if self.args.backend in ("lxd", "fake"):
            self.snap_store_set_proxy()
        for snap_name in self.core_snap_names:
            if snap_name in self.args.channels:
                self.backend.run(
                    ["snap", "install",
                     "--channel=%s" % self.args.channels[snap_name],
                     snap_name])
        if "charmcraft" in self.args.channels:
            self.backend.run(
                ["snap", "install",
                 "--channel=%s" % self.args.channels["charmcraft"],
                 "charmcraft"])
        else:
            self.backend.run(["snap", "install", "charmcraft"])
        # The charmcraft snap can't see /build, so we have to do our work under
        # /home/buildd instead.  Make sure it exists.
        self.backend.run(["mkdir", "-p", "/home/buildd"])

    def repo(self):
        """Collect git or bzr branch."""
        logger.info("Running repo phase...")
        self.vcs_fetch(self.args.name, cwd="/home/buildd")
        self.save_status(self.buildd_path)

    def build(self):
        logger.info("Running build phase...")
        build_context_path = os.path.join(
            "/home/buildd",
            self.args.name,
            self.args.build_path)
        check_path_escape(self.buildd_path, build_context_path)
        args = ["charmcraft", "build", "-v", "-f", build_context_path]
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

