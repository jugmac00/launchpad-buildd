# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

from argparse import ArgumentParser
import logging
import sys

from lpbuildd.target.backend import Backend


class Operation:
    """An operation to perform on the target environment."""

    def __init__(self, args=None):
        self.parse_args(args=args)
        self.backend = Backend.get(
            self.args.backend, self.args.build_id,
            series=self.args.series, arch=self.args.arch)

    @property
    def description(self):
        """A description of this operation, passed to the argument parser."""
        raise NotImplementedError

    def make_parser(self):
        parser = ArgumentParser(description=self.description)
        parser.add_argument(
            "--backend", choices=["chroot", "lxd", "fake"],
            help="use this type of backend")
        parser.add_argument(
            "--series", metavar="SERIES", help="operate on series SERIES")
        parser.add_argument(
            "--arch", metavar="ARCH", help="operate on architecture ARCH")
        parser.add_argument(
            "build_id", metavar="ID", help="operate on build ID")
        return parser

    def parse_args(self, args=None):
        self.args = self.make_parser().parse_args(args=args)

    def run(self):
        raise NotImplementedError


def configure_logging():
    class StdoutFilter(logging.Filter):
        def filter(self, record):
            return record.levelno <= logging.WARNING

    class StderrFilter(logging.Filter):
        def filter(self, record):
            return record.levelno >= logging.ERROR

    logger = logging.getLogger()
    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.addFilter(StdoutFilter())
    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.addFilter(StderrFilter())
    for handler in (stdout_handler, stderr_handler):
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
