# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import os.path
import subprocess


class BackendException(Exception):
    pass


class Backend:
    """A backend implementation for the environment where we run builds."""

    def __init__(self, build_id, series=None, arch=None):
        self.build_id = build_id
        self.series = series
        self.arch = arch
        self.build_path = os.path.join(os.environ["HOME"], "build-" + build_id)

    def create(self, tarball_path):
        """Create the backend based on a chroot tarball.

        This puts the backend into a state where it is ready to be started.
        """
        raise NotImplementedError

    def run(self, args, env=None, input_text=None, **kwargs):
        """Run a command in the target environment.

        :param args: the command and arguments to run.
        :param env: additional environment variables to set.
        :param input_text: input text to pass on the command's stdin.
        :param kwargs: additional keyword arguments for `subprocess.Popen`.
        """
        raise NotImplementedError

    def copy_in(self, source_path, target_path):
        """Copy a file into the target environment.

        The target file will be owned by root/root and have the same
        permission mode as the source file.

        :param source_path: the path to the file that should be copied from
            the host system.
        :param target_path: the path where the file should be installed
            inside the target environment, relative to the target
            environment's root.
        """
        raise NotImplementedError

    def remove(self):
        """Remove the backend."""
        subprocess.check_call(["sudo", "rm", "-rf", self.build_path])


def make_backend(name, build_id, series=None, arch=None):
    if name == "chroot":
        from lpbuildd.target.chroot import Chroot
        backend_factory = Chroot
    else:
        raise KeyError("Unknown backend: %s" % name)
    return backend_factory(build_id, series=series, arch=arch)
