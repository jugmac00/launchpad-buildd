# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import os.path
import signal
import stat
import subprocess
import time

from lpbuildd.target.backend import Backend, BackendException
from lpbuildd.util import set_personality, shell_escape


class Chroot(Backend):
    """Sets up a chroot."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chroot_path = os.path.join(self.build_path, "chroot-autobuild")

    def create(self, image_path, image_type):
        """See `Backend`."""
        if image_type == "chroot":
            subprocess.check_call(
                ["sudo", "tar", "-C", self.build_path, "-xf", image_path]
            )
        else:
            raise ValueError("Unhandled image type: %s" % image_type)

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

    def run(
        self,
        args,
        cwd=None,
        env=None,
        input_text=None,
        get_output=False,
        echo=False,
        **kwargs,
    ):
        """See `Backend`."""
        if env:
            args = (
                ["env"]
                + [f"{key}={value}" for key, value in env.items()]
                + args
            )
        if self.arch is not None:
            args = set_personality(args, self.arch, series=self.series)
        if cwd is not None:
            # This requires either a helper program in the chroot or
            # unpleasant quoting.  For now we go for the unpleasant quoting,
            # though once we have coreutils >= 8.28 everywhere we'll be able
            # to use "env --chdir".
            escaped_args = " ".join(shell_escape(arg) for arg in args)
            args = [
                "/bin/sh",
                "-c",
                f"cd {shell_escape(cwd)} && {escaped_args}",
            ]
        if echo:
            print(
                "Running in chroot: %s"
                % " ".join(shell_escape(arg) for arg in args)
            )
        cmd = ["sudo", "/usr/sbin/chroot", self.chroot_path] + args
        if input_text is None and not get_output:
            subprocess.check_call(cmd, **kwargs)
        else:
            if get_output:
                kwargs["stdout"] = subprocess.PIPE
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, **kwargs)
            output, _ = proc.communicate(input_text)
            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, cmd)
            if get_output:
                if echo:
                    print("Output:")
                    output_text = output
                    if isinstance(output_text, bytes):
                        output_text = output_text.decode("UTF-8", "replace")
                    print(output_text)
                return output

    def copy_in(self, source_path, target_path):
        """See `Backend`."""
        # Use install(1) so that we can end up with root/root ownership with
        # a minimum of subprocess calls; the buildd user may not make sense
        # in the target.
        mode = stat.S_IMODE(os.stat(source_path).st_mode)
        full_target_path = os.path.join(
            self.chroot_path, target_path.lstrip("/")
        )
        subprocess.check_call(
            [
                "sudo",
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "%o" % mode,
                source_path,
                full_target_path,
            ]
        )

    def copy_out(self, source_path, target_path):
        # Don't use install(1) here because running `os.stat` to get file mode
        # may be impossible. Instead, copy the with `cp` and set file ownership
        # to buildd (this is necessary so that buildd can read/write the copied
        # file).
        full_source_path = os.path.join(
            self.chroot_path, source_path.lstrip("/")
        )
        subprocess.check_call(
            [
                "sudo",
                "cp",
                "--preserve=timestamps",
                full_source_path,
                target_path,
            ]
        )
        uid, gid = os.getuid(), os.getgid()
        subprocess.check_call(["sudo", "chown", f"{uid}:{gid}", target_path])

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
                subprocess.call(["sudo", "umount", mount]) for mount in mounts
            ]
            if any(retcodes):
                time.sleep(1)
        else:
            if list(self._get_chroot_mounts()):
                subprocess.check_call(["lsof", self.chroot_path])
                raise BackendException(
                    "Failed to unmount %s" % self.chroot_path
                )
