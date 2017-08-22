# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import os.path
import stat
import subprocess

from lpbuildd.target.backend import Backend
from lpbuildd.util import (
    set_personality,
    shell_escape,
    )


class Chroot(Backend):
    """Sets up a chroot."""

    def __init__(self, build_id, series=None, arch=None):
        super(Chroot, self).__init__(build_id, series=series, arch=arch)
        self.chroot_path = os.path.join(self.build_path, "chroot-autobuild")

    def run(self, args, env=None, input_text=None, **kwargs):
        """See `Backend`."""
        if env:
            args = ["env"] + [
                "%s=%s" % (key, shell_escape(value))
                for key, value in env.items()] + args
        if self.arch is not None:
            args = set_personality(args, self.arch, series=self.series)
        cmd = ["sudo", "/usr/sbin/chroot", self.chroot_path] + args
        if input_text is None:
            subprocess.check_call(cmd, cwd=self.chroot_path, **kwargs)
        else:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, universal_newlines=True, **kwargs)
            proc.communicate(input_text)
            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, cmd)

    def copy_in(self, source_path, target_path):
        """See `Backend`."""
        # Use install(1) so that we can end up with root/root ownership with
        # a minimum of subprocess calls; the buildd user may not make sense
        # in the target.
        mode = stat.S_IMODE(os.stat(source_path).st_mode)
        full_target_path = os.path.join(
            self.chroot_path, target_path.lstrip("/"))
        subprocess.check_call(
            ["sudo", "install", "-o", "root", "-g", "root", "-m", "%o" % mode,
             source_path, full_target_path])
