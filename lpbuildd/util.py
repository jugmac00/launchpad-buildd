# Copyright 2015-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import os
import subprocess
import sys
from shlex import quote
from urllib.parse import urlparse

import requests


def shell_escape(s):
    # It's sometimes necessary to pass arguments as bytes to avoid
    # locale-dependent problems, but Python 3's shlex.quote doesn't like
    # that, so work around it.
    if sys.version_info[0] >= 3 and isinstance(s, bytes):
        return quote(s.decode("UTF-8")).encode("UTF-8")
    else:
        return quote(s)


def get_arch_bits(arch):
    if arch == "x32":
        # x32 is an exception: the userspace is 32-bit, but it expects to be
        # running on a 64-bit kernel.
        return 64
    else:
        env = dict(os.environ)
        env.pop("DEB_HOST_ARCH_BITS", None)
        bits = subprocess.check_output(
            ["dpkg-architecture", "-a%s" % arch, "-qDEB_HOST_ARCH_BITS"],
            env=env,
            universal_newlines=True,
        ).rstrip("\n")
        if bits == "32":
            return 32
        elif bits == "64":
            return 64
        else:
            raise RuntimeError(
                "Don't know how to deal with architecture %s "
                "(DEB_HOST_ARCH_BITS=%s)" % (arch, bits)
            )


def set_personality(args, arch, series=None):
    bits = get_arch_bits(arch)
    assert bits in (32, 64)
    if bits == 32:
        setarch_cmd = ["linux32"]
    else:
        setarch_cmd = ["linux64"]

    if series in ("hardy", "lucid", "maverick", "natty", "oneiric", "precise"):
        setarch_cmd.append("--uname-2.6")

    return setarch_cmd + args


class RevokeProxyTokenError(Exception):
    def __init__(self, username, exception):
        super().__init__(self)
        self.username = username
        self.exception = exception

    def __str__(self):
        return f"Unable to revoke token for {self.username}: {self.exception}"


def revoke_proxy_token(proxy_url, revocation_endpoint):
    """Revoke builder proxy token.

    :raises RevokeProxyTokenError: if attempting to revoke the token failed.
    """
    url = urlparse(proxy_url)
    try:
        requests.delete(
            revocation_endpoint, auth=(url.username, url.password), timeout=15
        )
    except requests.RequestException as e:
        raise RevokeProxyTokenError(url.username, e)
