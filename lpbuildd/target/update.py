# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import logging
import subprocess
import time

from lpbuildd.target.operation import Operation


logger = logging.getLogger(__name__)


class Update(Operation):

    description = "Update the target environment."

    def run(self):
        logger.info("Updating target for build %s", self.args.build_id)
        with open("/dev/null", "r") as devnull:
            env = {
                "LANG": "C",
                "DEBIAN_FRONTEND": "noninteractive",
                "TTY": "unknown",
                }
            apt_get = "/usr/bin/apt-get"
            update_args = [apt_get, "-uy", "update"]
            try:
                self.backend.run(update_args, env=env, stdin=devnull)
            except subprocess.CalledProcessError:
                logger.warning("Waiting 15 seconds and trying again ...")
                time.sleep(15)
                self.backend.run(update_args, env=env, stdin=devnull)
            upgrade_args = [
                apt_get, "-o", "DPkg::Options::=--force-confold", "-uy",
                "--purge", "dist-upgrade",
                ]
            self.backend.run(upgrade_args, env=env, stdin=devnull)
        return 0
