# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import requests
from six.moves.urllib.parse import (
    urljoin,
    urlparse,
    urlunparse,
    )


class SnapStoreOperationMixin:
    """Methods supporting operations that interact with the snap store."""

    @classmethod
    def add_arguments(cls, parser):
        super(SnapStoreOperationMixin, cls).add_arguments(parser)
        parser.add_argument(
            "--snap-store-proxy-url", metavar="URL",
            help="snap store proxy URL")

    def snap_store_set_proxy(self):
        if self.args.snap_store_proxy_url is None:
            return
        # Canonicalise: proxy registration always sends only the scheme and
        # domain.
        parsed_url = urlparse(self.args.snap_store_proxy_url)
        canonical_url = urlunparse(
            [parsed_url.scheme, parsed_url.netloc, "", "", "", ""])
        assertions_response = requests.get(
            urljoin(canonical_url, "v2/auth/store/assertions"))
        assertions_response.raise_for_status()
        self.backend.run(
            ["snap", "ack", "/dev/stdin"], input_text=assertions_response.text)
        store_assertion = self.backend.run(
            ["snap", "known", "store", "url={}".format(canonical_url)],
            get_output=True)
        # Very cheap parser.  Not at all robust, but snapd has already
        # handled validation for us, and if we get more than one assertion
        # back from "snap known" despite filtering then we only care about
        # the first.
        for line in store_assertion.split("\n\n")[0].splitlines():
            if line.startswith("store: "):
                store_id = line[len("store: "):]
                break
        else:
            store_id = None
        if store_id is not None:
            self.backend.run(
                ["snap", "set", "core", "proxy.store={}".format(store_id)])
