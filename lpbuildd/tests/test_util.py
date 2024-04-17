# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import base64

import responses
from testtools import TestCase

from lpbuildd.util import (
    get_arch_bits,
    revoke_proxy_token,
    set_personality,
    shell_escape,
)


class TestShellEscape(TestCase):
    def test_plain(self):
        self.assertEqual("foo", shell_escape("foo"))

    def test_whitespace(self):
        self.assertEqual("'  '", shell_escape("  "))

    def test_single_quotes(self):
        self.assertEqual(
            "'shell'\"'\"'s great'", shell_escape("shell's great")
        )

    def test_bytes(self):
        self.assertEqual(
            "'\N{SNOWMAN}'".encode(), shell_escape("\N{SNOWMAN}".encode())
        )


class TestGetArchBits(TestCase):
    def test_x32(self):
        self.assertEqual(64, get_arch_bits("x32"))

    def test_32bit(self):
        self.assertEqual(32, get_arch_bits("armhf"))
        self.assertEqual(32, get_arch_bits("i386"))

    def test_64bit(self):
        self.assertEqual(64, get_arch_bits("amd64"))
        self.assertEqual(64, get_arch_bits("arm64"))


class TestSetPersonality(TestCase):
    def test_32bit(self):
        self.assertEqual(
            ["linux32", "sbuild"], set_personality(["sbuild"], "i386")
        )

    def test_64bit(self):
        self.assertEqual(
            ["linux64", "sbuild"], set_personality(["sbuild"], "amd64")
        )

    def test_uname_26(self):
        self.assertEqual(
            ["linux64", "--uname-2.6", "sbuild"],
            set_personality(["sbuild"], "amd64", series="precise"),
        )

    def test_no_uname_26(self):
        self.assertEqual(
            ["linux64", "sbuild"],
            set_personality(["sbuild"], "amd64", series="trusty"),
        )


class TestRevokeToken(TestCase):
    @responses.activate
    def test_revoke_proxy_token(self):
        """Proxy token revocation uses the right authentication"""

        proxy_url = "http://username:password@proxy.example"
        revocation_endpoint = "http://proxy-auth.example/tokens/build_id"
        token = base64.b64encode(b"username:password").decode()

        responses.add(responses.DELETE, revocation_endpoint)

        revoke_proxy_token(proxy_url, revocation_endpoint)
        self.assertEqual(1, len(responses.calls))
        request = responses.calls[0].request
        self.assertEqual(
            "http://proxy-auth.example/tokens/build_id", request.url
        )
        self.assertEqual(f"Basic {token}", request.headers["Authorization"])

    @responses.activate
    def test_revoke_fetch_service_token(self):
        """Proxy token revocation for the fetch service"""

        token = "token"
        proxy_url = f"http://session_id:{token}@proxy.fetch-service.example"
        revocation_endpoint = (
            "http://control.fetch-service.example/session_id/token"
        )

        responses.add(responses.DELETE, revocation_endpoint)

        revoke_proxy_token(
            proxy_url,
            revocation_endpoint,
            use_fetch_service=True,
        )

        self.assertEqual(1, len(responses.calls))
        request = responses.calls[0].request
        self.assertEqual(
            "http://control.fetch-service.example/session_id/token",
            request.url,
        )
        self.assertEqual(f"Basic {token}", request.headers["Authorization"])
