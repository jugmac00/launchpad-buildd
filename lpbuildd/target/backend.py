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

    def start(self):
        """Start the backend.

        This puts the backend into a state where it can run commands.
        """
        raise NotImplementedError

    def run(self, args, env=None, input_text=None, get_output=False,
            echo=False, **kwargs):
        """Run a command in the target environment.

        :param args: the command and arguments to run.
        :param env: additional environment variables to set.
        :param input_text: input text to pass on the command's stdin.
        :param get_output: if True, return the output from the command.
        :param echo: if True, print the command before executing it.
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

    def copy_out(self, source_path, target_path):
        """Copy a file out of the target environment.

        The target file will have the same permission mode as the source
        file.

        :param source_path: the path to the file that should be copied,
            relative to the target environment's root.
        :param target_path: the path where the file should be installed in
            the host system.
        """
        raise NotImplementedError

    def path_exists(self, path):
        """Test whether a path exists in the target environment.

        :param path: the path to the file to test, relative to the target
            environment's root.
        """
        try:
            self.run(["test", "-e", path])
            return True
        except subprocess.CalledProcessError:
            return False

    def islink(self, path):
        """Test whether a file is a symbolic link in the target environment.

        :param path: the path to the file to test, relative to the target
            environment's root.
        """
        try:
            self.run(["test", "-h", path])
            return True
        except subprocess.CalledProcessError:
            return False

    def listdir(self, path):
        """List a directory in the target environment.

        :param path: the path to the directory to list, relative to the
            target environment's root.
        """
        paths = self.run(
            ["find", path, "-mindepth", "1", "-maxdepth", "1",
             "-printf", "%P\\0"],
            get_output=True).rstrip(b"\0").split(b"\0")
        # XXX cjwatson 2017-08-04: Use `os.fsdecode` instead once we're on
        # Python 3.
        return [path.decode("UTF-8") for path in paths]

    def kill_processes(self):
        """Kill any processes left running in the target.

        This is allowed to do nothing if stopping the target will reliably
        kill all processes running in it.
        """
        # XXX cjwatson 2017-08-22: It might make sense to merge this into
        # `stop` later.
        pass

    def stop(self):
        """Stop the backend."""
        raise NotImplementedError

    def remove(self):
        """Remove the backend."""
        subprocess.check_call(["sudo", "rm", "-rf", self.build_path])


def make_backend(name, build_id, series=None, arch=None):
    if name == "chroot":
        from lpbuildd.target.chroot import Chroot
        backend_factory = Chroot
    elif name == "fake":
        # Only for use in tests.
        from lpbuildd.tests.fakeslave import FakeBackend
        backend_factory = FakeBackend
    else:
        raise KeyError("Unknown backend: %s" % name)
    return backend_factory(build_id, series=series, arch=arch)