# Copyright 2009-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import logging
import os
import tempfile

from lpbuildd.target.operation import Operation


logger = logging.getLogger(__name__)


class OverrideSourcesList(Operation):

    description = "Override sources.list in the target environment."

    def make_parser(self):
        parser = super(OverrideSourcesList, self).make_parser()
        parser.add_argument(
            "archives", metavar="ARCHIVE", nargs="+",
            help="sources.list lines")
        return parser

    def run(self):
        logger.info("Overriding sources.list in build-%s", self.args.build_id)
        with tempfile.NamedTemporaryFile() as sources_list:
            for archive in self.args.archives:
                print(archive, file=sources_list)
            sources_list.flush()
            os.fchmod(sources_list.fileno(), 0o644)
            self.backend.copy_in(sources_list.name, "/etc/apt/sources.list")
        return 0
