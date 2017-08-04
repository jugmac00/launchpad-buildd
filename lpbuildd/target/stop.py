# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import logging

from lpbuildd.target.backend import BackendException
from lpbuildd.target.operation import Operation


logger = logging.getLogger(__name__)


class Stop(Operation):

    description = "Stop the target environment."

    def run(self):
        logger.info("Stopping target for build %s", self.args.build_id)
        try:
            self.backend.stop()
        except BackendException:
            logger.exception('Failed to stop target')
            return 1
        return 0
