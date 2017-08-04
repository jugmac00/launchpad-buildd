# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import logging

from lpbuildd.target.operation import Operation


logger = logging.getLogger(__name__)


class Create(Operation):

    description = "Create the target environment."

    def make_parser(self):
        parser = super(Create, self).make_parser()
        parser.add_argument("tarball_path", help="path to chroot tarball")
        return parser

    def run(self):
        logger.info("Creating target for build %s", self.args.build_id)
        self.backend.create(self.args.tarball_path)
        return 0
