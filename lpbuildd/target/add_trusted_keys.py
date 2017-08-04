# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import logging
import sys

from lpbuildd.target.operation import Operation


logger = logging.getLogger(__name__)


class AddTrustedKeys(Operation):

    description = "Write out new trusted keys."

    def __init__(self, args=None, input_file=None):
        super(AddTrustedKeys, self).__init__(args=args)
        self.input_file = input_file or sys.stdin

    def run(self):
        """Add trusted keys from an input file."""
        logger.info("Adding trusted keys to build-%s", self.args.build_id)
        self.backend.run(["apt-key", "add", "-"], stdin=self.input_file)
        self.backend.run(["apt-key", "list"])
        return 0
