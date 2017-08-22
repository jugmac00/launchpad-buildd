# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import logging
import os
import sys

from lpbuildd.target.operation import Operation


logger = logging.getLogger(__name__)


class KillProcesses(Operation):

    description = "Kill any processes in the target."

    def run(self):
        # This operation must run as root, since we want to iterate over
        # other users' processes in Python.
        if os.geteuid() != 0:
            cmd = ["sudo"]
            if "PYTHONPATH" in os.environ:
                cmd.append("PYTHONPATH=%s" % os.environ["PYTHONPATH"])
            cmd.append("--")
            cmd.extend(sys.argv)
            os.execv("/usr/bin/sudo", cmd)
        return self._run()

    def _run(self):
        logger.info(
            "Scanning for processes to kill in build %s", self.args.build_id)
        self.backend.kill_processes()
        return 0
