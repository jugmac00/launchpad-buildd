# Copyright 2013-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

from collections import OrderedDict
import logging
import os

from lpbuildd.target.operation import Operation
from lpbuildd.util import shell_escape


RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


logger = logging.getLogger(__name__)


def get_build_path(build_id, *extra):
    """Generate a path within the build directory.

    :param build_id: the build id to use.
    :param extra: the extra path segments within the build directory.
    :return: the generated path.
    """
    return os.path.join(os.environ["HOME"], "build-" + build_id, *extra)


class BuildLiveFS(Operation):

    description = "Build a live file system."

    @classmethod
    def add_arguments(cls, parser):
        super(BuildLiveFS, cls).add_arguments(parser)
        parser.add_argument(
            "--subarch", metavar="SUBARCH",
            help="build for subarchitecture SUBARCH")
        parser.add_argument(
            "--project", metavar="PROJECT", help="build for project PROJECT")
        parser.add_argument(
            "--subproject", metavar="SUBPROJECT",
            help="build for subproject SUBPROJECT")
        parser.add_argument("--datestamp", help="date stamp")
        parser.add_argument(
            "--image-format", metavar="FORMAT",
            help="produce an image in FORMAT")
        parser.add_argument(
            "--proposed", default=False, action="store_true",
            help="enable use of -proposed pocket")
        parser.add_argument(
            "--locale", metavar="LOCALE",
            help="use ubuntu-defaults-image to build an image for LOCALE")
        parser.add_argument(
            "--extra-ppa", dest="extra_ppas", default=[], action="append",
            help="use this additional PPA")

    def run_build_command(self, args, env=None, echo=False):
        """Run a build command in the chroot.

        This is unpleasant because we need to run it in /build under sudo
        chroot, and there's no way to do this without either a helper
        program in the chroot or unpleasant quoting.  We go for the
        unpleasant quoting.

        :param args: the command and arguments to run.
        :param env: dictionary of additional environment variables to set.
        :param echo: if True, print the command before executing it.
        """
        args = [shell_escape(arg) for arg in args]
        if env:
            args = ["env"] + [
                "%s=%s" % (key, shell_escape(value))
                for key, value in env.items()] + args
        command = "cd /build && %s" % " ".join(args)
        self.backend.run(["/bin/sh", "-c", command], echo=echo)

    def install(self):
        deps = ["livecd-rootfs"]
        if self.args.backend == "lxd":
            deps.extend(["snapd", "fuse", "squashfuse"])
        self.backend.run(["apt-get", "-y", "install"] + deps)
        if self.args.arch == "i386":
            self.backend.run([
                "apt-get", "-y", "--no-install-recommends", "install",
                "ltsp-server",
                ])
        if self.args.locale is not None:
            self.backend.run([
                "apt-get", "-y", "--install-recommends", "install",
                "ubuntu-defaults-builder",
                ])

    def build(self):
        if self.args.locale is not None:
            self.run_build_command([
                "ubuntu-defaults-image",
                "--locale", self.args.locale,
                "--arch", self.args.arch,
                "--release", self.args.series,
                ])
        else:
            self.run_build_command(["rm", "-rf", "auto"])
            self.run_build_command(["mkdir", "-p", "auto"])
            for lb_script in ("config", "build", "clean"):
                lb_script_path = os.path.join(
                    "/usr/share/livecd-rootfs/live-build/auto", lb_script)
                self.run_build_command(["ln", "-s", lb_script_path, "auto/"])
            self.run_build_command(["lb", "clean", "--purge"])

            base_lb_env = OrderedDict()
            base_lb_env["PROJECT"] = self.args.project
            base_lb_env["ARCH"] = self.args.arch
            if self.args.subproject is not None:
                base_lb_env["SUBPROJECT"] = self.args.subproject
            if self.args.subarch is not None:
                base_lb_env["SUBARCH"] = self.args.subarch
            lb_env = base_lb_env.copy()
            lb_env["SUITE"] = self.args.series
            if self.args.datestamp is not None:
                lb_env["NOW"] = self.args.datestamp
            if self.args.image_format is not None:
                lb_env["IMAGEFORMAT"] = self.args.image_format
            if self.args.proposed:
                lb_env["PROPOSED"] = "1"
            if self.args.extra_ppas:
                lb_env["EXTRA_PPAS"] = " ".join(self.args.extra_ppas)
            self.run_build_command(["lb", "config"], env=lb_env)
            self.run_build_command(["lb", "build"], env=base_lb_env)

    def run(self):
        try:
            self.install()
        except Exception:
            logger.exception('Install failed')
            return RETCODE_FAILURE_INSTALL
        try:
            self.build()
        except Exception:
            logger.exception('Build failed')
            return RETCODE_FAILURE_BUILD
        return 0
