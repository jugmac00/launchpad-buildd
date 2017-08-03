# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import os.path
import subprocess
import sys
import tempfile
import time

from lpbuildd.util import (
    set_personality,
    shell_escape,
    )


class ChrootSetup:
    """Sets up a chroot."""

    def __init__(self, build_id, series=None, arch=None):
        self.build_id = build_id
        self.series = series
        self.arch = arch
        self.chroot_path = os.path.join(
            os.environ["HOME"], "build-" + build_id, "chroot-autobuild")

    def chroot(self, args, env=None, input_text=None, **kwargs):
        """Run a command in the chroot.

        :param args: the command and arguments to run.
        """
        if env:
            args = ["env"] + [
                "%s=%s" % (key, shell_escape(value))
                for key, value in env.items()] + args
        if self.arch is not None:
            args = set_personality(args, self.arch, series=self.series)
        cmd = ["/usr/bin/sudo", "/usr/sbin/chroot", self.chroot_path] + args
        if input_text is None:
            subprocess.check_call(cmd, **kwargs)
        else:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, universal_newlines=True, **kwargs)
            proc.communicate(input_text)
            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, cmd)

    def insert_file(self, source_path, target_path, mode=0o644):
        """Insert a file into the chroot.

        :param source_path: the path to the file outside the chroot.
        :param target_path: the path where the file should be installed
            inside the chroot.
        """
        full_target_path = os.path.join(
            self.chroot_path, target_path.lstrip("/"))
        subprocess.check_call(
            ["/usr/bin/sudo", "install",
             "-o", "root", "-g", "root", "-m", "%o" % mode,
             source_path, full_target_path])

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

    def override_sources_list(self, archives):
        with tempfile.NamedTemporaryFile() as sources_list:
            for archive in archives:
                print(archive, file=sources_list)
            sources_list.flush()
            self.insert_file(sources_list.name, "/etc/apt/sources.list")
