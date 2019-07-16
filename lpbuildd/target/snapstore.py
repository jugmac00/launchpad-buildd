# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import os

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
        store_id = assertions_response.headers.get("X-Assertion-Store-Id")
        if store_id is not None:
            self.backend.run(
                ["snap", "set", "core", "proxy.store={}".format(store_id)])


class SnapStoreProxyMixin:

    @classmethod
    def add_arguments(cls, parser):
        super(SnapStoreProxyMixin, cls).add_arguments(parser)
        parser.add_argument("--proxy-url", help="builder proxy url")
        parser.add_argument(
            "--revocation-endpoint",
            help="builder proxy token revocation endpoint")

    def install(self):
        deps = []
        if self.args.proxy_url:
            deps.extend(["python3", "socat"])
        if self.args.proxy_url:
            self.backend.copy_in(
                os.path.join(self.bin, "snap-git-proxy"),
                "/usr/local/bin/snap-git-proxy")
        return deps
