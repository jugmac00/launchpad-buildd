# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import logging

from lpbuildd.target.operation import Operation


logger = logging.getLogger(__name__)


class Remove(Operation):

    description = "Remove the target environment."

    def run(self):
        logger.info("Removing build %s", self.args.build_id)
        self.backend.remove()
        return 0
