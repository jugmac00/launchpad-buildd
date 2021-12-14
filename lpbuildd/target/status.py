# Copyright 2018-2021 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import json
import os


class StatusOperationMixin:
    """Methods supporting operations that save extra status information.

    Extra status information will be picked up by the build manager and
    included in XML-RPC status responses.
    """

    def __init__(self, args, parser):
        super(StatusOperationMixin, self).__init__(args, parser)
        self._status = {}

    def get_status(self):
        """Return a copy of this operation's extra status."""
        return dict(self._status)

    def update_status(self, **status):
        """Update this operation's status with key/value pairs."""
        self._status.update(status)
        status_path = os.path.join(self.backend.build_path, "status")
        with open("%s.tmp" % status_path, "w") as status_file:
            json.dump(self._status, status_file)
        os.rename("%s.tmp" % status_path, status_path)
