# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import os.path
import subprocess
import sys
import time

from lpbuildd.util import (
    set_personality,
    shell_escape,
    )


class ChrootUpdater:
    """Updates a chroot."""

    def __init__(self, build_id, series, arch):
        self.build_id = build_id
        self.series = series
        self.arch = arch
        self.chroot_path = os.path.join(
            os.environ["HOME"], "build-" + build_id, "chroot-autobuild")

    def chroot(self, args, env=None, **kwargs):
        """Run a command in the chroot.

        :param args: the command and arguments to run.
        """
        if env:
            args = ["env"] + [
                "%s=%s" % (key, shell_escape(value))
                for key, value in env.items()] + args
        args = set_personality(args, self.arch, series=self.series)
        cmd = ["/usr/bin/sudo", "/usr/sbin/chroot", self.chroot_path] + args
        subprocess.check_call(cmd, **kwargs)

    def update(self):
        with open("/dev/null", "r") as devnull:
            env = {
                "LANG": "C",
                "DEBIAN_FRONTEND": "noninteractive",
                "TTY": "unknown",
                }
            apt_get = "/usr/bin/apt-get"
            update_args = [apt_get, "-uy", "update"]
            try:
                self.chroot(update_args, env=env, stdin=devnull)
            except subprocess.CalledProcessError:
                print(
                    "Waiting 15 seconds and trying again ...", file=sys.stderr)
                time.sleep(15)
                self.chroot(update_args, env=env, stdin=devnull)
            upgrade_args = [
                apt_get, "-o", "DPkg::Options::=--force-confold", "-uy",
                "--purge", "dist-upgrade",
                ]
            self.chroot(upgrade_args, env=env, stdin=devnull)
