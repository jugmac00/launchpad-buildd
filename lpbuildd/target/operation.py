# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import os

from lpbuildd.target.backend import make_backend


class Operation:
    """An operation to perform on the target environment."""

    description = "An unidentified operation."

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            "--backend", choices=["chroot", "lxd", "fake", "uncontained"],
            help="use this type of backend")
        parser.add_argument(
            "--series", metavar="SERIES", help="operate on series SERIES")
        parser.add_argument(
            "--arch", metavar="ARCH", help="operate on architecture ARCH")
        parser.add_argument(
            "build_id", metavar="ID", help="operate on build ID")

    def __init__(self, args, parser):
        self.args = args
        self.backend = make_backend(
            self.args.backend, self.args.build_id,
            series=self.args.series, arch=self.args.arch)

    def run(self):
        raise NotImplementedError

    def _check_path_escape(self, path_to_check):
        """Check the build file path doesn't escape the build directory."""
        build_file_path = os.path.realpath(
            os.path.join(self.buildd_path, path_to_check))
        common_path = os.path.commonprefix((build_file_path, self.buildd_path))
        if common_path != self.buildd_path:
            raise InvalidBuildFilePath("Invalid build file path.")
