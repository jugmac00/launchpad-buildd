# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import logging

from lpbuildd.target.operation import Operation


logger = logging.getLogger(__name__)


class KillProcesses(Operation):

    description = "Kill any processes in the target."

    def run(self):
        logger.info(
            "Scanning for processes to kill in build %s", self.args.build_id)
        self.backend.kill_processes()
        return 0
