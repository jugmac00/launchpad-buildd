# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import os.path
import signal
import stat
import subprocess
import time

from lpbuildd.target.backend import (
    Backend,
    BackendException,
    )
from lpbuildd.util import (
    set_personality,
    shell_escape,
    )


class Chroot(Backend):
    """Sets up a chroot."""

    def __init__(self, build_id, series=None, arch=None):
        super(Chroot, self).__init__(build_id, series=series, arch=arch)
        self.chroot_path = os.path.join(self.build_path, "chroot-autobuild")

    def create(self, tarball_path):
        """See `Backend`."""
        subprocess.check_call(
            ["sudo", "tar", "-C", self.build_path, "-xf", tarball_path])

    def start(self):
        """See `Backend`."""
        mounts = (
            ("proc", None, "none", "proc"),
            ("devpts", "gid=5,mode=620", "none", "dev/pts"),
            ("sysfs", None, "none", "sys"),
            ("tmpfs", None, "none", "dev/shm"),
            )
        for mount in mounts:
            cmd = ["sudo", "mount", "-t", mount[0]]
            if mount[1]:
                cmd.extend(["-o", mount[1]])
            cmd.append(mount[2])
            cmd.append(os.path.join(self.chroot_path, mount[3]))
            subprocess.check_call(cmd)

        for path in ("/etc/hosts", "/etc/hostname", "/etc/resolv.conf"):
            self.copy_in(path, path)

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

    def kill_processes(self):
        """See `Backend`."""
        prefix = os.path.realpath(self.chroot_path)
        while True:
            found = False
            pids = [int(pid) for pid in os.listdir("/proc") if pid.isdigit()]
            for pid in sorted(pids):
                try:
                    link = os.readlink(os.path.join("/proc", str(pid), "root"))
                except OSError:
                    continue
                if link and (link == prefix or link.startswith(prefix + "/")):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                    found = True
            if not found:
                break

    def _get_chroot_mounts(self):
        with open("/proc/mounts") as mounts_file:
            for line in mounts_file:
                mount_path = line.split()[1]
                if mount_path.startswith(self.chroot_path):
                    yield mount_path

    def stop(self):
        """See `Backend`."""
        for _ in range(20):
            # Reverse the list, since we must unmount subdirectories before
            # parent directories.
            mounts = reversed(list(self._get_chroot_mounts()))
            if not mounts:
                break
            retcodes = [
                subprocess.call(["sudo", "umount", mount])
                for mount in mounts]
            if any(retcodes):
                time.sleep(1)
        else:
            if list(self._get_chroot_mounts()):
                subprocess.check_call(["lsof", self.chroot_path])
                raise BackendException(
                    "Failed to unmount %s" % self.chroot_path)
