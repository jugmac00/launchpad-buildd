# Copyright 2017-2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import stat
import subprocess
import tempfile
import time
from textwrap import dedent

from fixtures import FakeLogger
from systemfixtures import FakeTime
from testtools import TestCase
from testtools.matchers import (
    ContainsDict,
    Equals,
    MatchesDict,
    MatchesListwise,
)

from lpbuildd.target.cli import parse_args
from lpbuildd.tests.fakebuilder import FakeMethod


class MockCopyIn(FakeMethod):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_bytes = None

    def __call__(self, source_path, *args, **kwargs):
        with open(source_path, "rb") as source:
            self.source_bytes = source.read()
        return super().__call__(source_path, *args, **kwargs)


class TestOverrideSourcesList(TestCase):
    def test_succeeds(self):
        args = [
            "override-sources-list",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "deb http://archive.ubuntu.com/ubuntu xenial main",
            "deb http://ppa.launchpad.net/launchpad/ppa/ubuntu xenial main",
        ]
        override_sources_list = parse_args(args=args).operation
        self.assertEqual(0, override_sources_list.run())
        self.assertEqual(
            (
                dedent(
                    """\
                deb http://archive.ubuntu.com/ubuntu xenial main
                deb http://ppa.launchpad.net/launchpad/ppa/ubuntu xenial main
                """
                ).encode("UTF-8"),
                stat.S_IFREG | 0o644,
            ),
            override_sources_list.backend.backend_fs["/etc/apt/sources.list"],
        )
        self.assertEqual(
            (b'Acquire::Retries "3";\n', stat.S_IFREG | 0o644),
            override_sources_list.backend.backend_fs[
                "/etc/apt/apt.conf.d/99retries"
            ],
        )
        self.assertEqual(
            (
                b'APT::Get::Always-Include-Phased-Updates "true";\n',
                stat.S_IFREG | 0o644,
            ),
            override_sources_list.backend.backend_fs[
                "/etc/apt/apt.conf.d/99phasing"
            ],
        )
        self.assertEqual(
            (
                b"Package: *\nPin: release a=*-proposed\nPin-Priority: 500\n",
                stat.S_IFREG | 0o644,
            ),
            override_sources_list.backend.backend_fs[
                "/etc/apt/preferences.d/proposed.pref"
            ],
        )
        self.assertEqual(
            (
                b"Package: *\nPin: release a=*-backports\nPin-Priority: 500\n",
                stat.S_IFREG | 0o644,
            ),
            override_sources_list.backend.backend_fs[
                "/etc/apt/preferences.d/backports.pref"
            ],
        )

    def test_apt_proxy(self):
        args = [
            "override-sources-list",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
            "--apt-proxy-url",
            "http://apt-proxy.example:3128/",
            "deb http://archive.ubuntu.com/ubuntu xenial main",
        ]
        override_sources_list = parse_args(args=args).operation
        self.assertEqual(0, override_sources_list.run())
        self.assertEqual(
            (
                dedent(
                    """\
                deb http://archive.ubuntu.com/ubuntu xenial main
                """
                ).encode("UTF-8"),
                stat.S_IFREG | 0o644,
            ),
            override_sources_list.backend.backend_fs["/etc/apt/sources.list"],
        )
        self.assertEqual(
            (b'Acquire::Retries "3";\n', stat.S_IFREG | 0o644),
            override_sources_list.backend.backend_fs[
                "/etc/apt/apt.conf.d/99retries"
            ],
        )
        self.assertEqual(
            (
                b'APT::Get::Always-Include-Phased-Updates "true";\n',
                stat.S_IFREG | 0o644,
            ),
            override_sources_list.backend.backend_fs[
                "/etc/apt/apt.conf.d/99phasing"
            ],
        )
        self.assertEqual(
            (
                dedent(
                    """\
                Acquire::http::Proxy "http://apt-proxy.example:3128/";
                """
                ).encode("UTF-8"),
                stat.S_IFREG | 0o644,
            ),
            override_sources_list.backend.backend_fs[
                "/etc/apt/apt.conf.d/99proxy"
            ],
        )


# Output of:
#     gpg --no-default-keyring \
#         --keyring /usr/share/keyrings/ubuntu-archive-keyring.gpg \
#         --armor --export --export-options export-minimal,export-clean \
#         F6ECB3762474EDA9D21B7022871920D1991BC93C
# (For test purposes, the exact key ID isn't particularly important.  This
# just needs to be some kind of valid GPG public key.)
TEST_GPG_KEY = dedent(
    """\
    -----BEGIN PGP PUBLIC KEY BLOCK-----

    mQINBFufwdoBEADv/Gxytx/LcSXYuM0MwKojbBye81s0G1nEx+lz6VAUpIUZnbkq
    dXBHC+dwrGS/CeeLuAjPRLU8AoxE/jjvZVp8xFGEWHYdklqXGZ/gJfP5d3fIUBtZ
    HZEJl8B8m9pMHf/AQQdsC+YzizSG5t5Mhnotw044LXtdEEkx2t6Jz0OGrh+5Ioxq
    X7pZiq6Cv19BohaUioKMdp7ES6RYfN7ol6HSLFlrMXtVfh/ijpN9j3ZhVGVeRC8k
    KHQsJ5PkIbmvxBiUh7SJmfZUx0IQhNMaDHXfdZAGNtnhzzNReb1FqNLSVkrS/Pns
    AQzMhG1BDm2VOSF64jebKXffFqM5LXRQTeqTLsjUbbrqR6s/GCO8UF7jfUj6I7ta
    LygmsHO/JD4jpKRC0gbpUBfaiJyLvuepx3kWoqL3sN0LhlMI80+fA7GTvoOx4tpq
    VlzlE6TajYu+jfW3QpOFS5ewEMdL26hzxsZg/geZvTbArcP+OsJKRmhv4kNo6Ayd
    yHQ/3ZV/f3X9mT3/SPLbJaumkgp3Yzd6t5PeBu+ZQk/mN5WNNuaihNEV7llb1Zhv
    Y0Fxu9BVd/BNl0rzuxp3rIinB2TX2SCg7wE5xXkwXuQ/2eTDE0v0HlGntkuZjGow
    DZkxHZQSxZVOzdZCRVaX/WEFLpKa2AQpw5RJrQ4oZ/OfifXyJzP27o03wQARAQAB
    tEJVYnVudHUgQXJjaGl2ZSBBdXRvbWF0aWMgU2lnbmluZyBLZXkgKDIwMTgpIDxm
    dHBtYXN0ZXJAdWJ1bnR1LmNvbT6JAjgEEwEKACIFAlufwdoCGwMGCwkIBwMCBhUI
    AgkKCwQWAgMBAh4BAheAAAoJEIcZINGZG8k8LHMQAKS2cnxz/5WaoCOWArf5g6UH
    beOCgc5DBm0hCuFDZWWv427aGei3CPuLw0DGLCXZdyc5dqE8mvjMlOmmAKKlj1uG
    g3TYCbQWjWPeMnBPZbkFgkZoXJ7/6CB7bWRht1sHzpt1LTZ+SYDwOwJ68QRp7DRa
    Zl9Y6QiUbeuhq2DUcTofVbBxbhrckN4ZteLvm+/nG9m/ciopc66LwRdkxqfJ32Cy
    q+1TS5VaIJDG7DWziG+Kbu6qCDM4QNlg3LH7p14CrRxAbc4lvohRgsV4eQqsIcdF
    kuVY5HPPj2K8TqpY6STe8Gh0aprG1RV8ZKay3KSMpnyV1fAKn4fM9byiLzQAovC0
    LZ9MMMsrAS/45AvC3IEKSShjLFn1X1dRCiO6/7jmZEoZtAp53hkf8SMBsi78hVNr
    BumZwfIdBA1v22+LY4xQK8q4XCoRcA9G+pvzU9YVW7cRnDZZGl0uwOw7z9PkQBF5
    KFKjWDz4fCk+K6+YtGpovGKekGBb8I7EA6UpvPgqA/QdI0t1IBP0N06RQcs1fUaA
    QEtz6DGy5zkRhR4pGSZn+dFET7PdAjEK84y7BdY4t+U1jcSIvBj0F2B7LwRL7xGp
    SpIKi/ekAXLs117bvFHaCvmUYN7JVp1GMmVFxhIdx6CFm3fxG8QjNb5tere/YqK+
    uOgcXny1UlwtCUzlrSaP
    =9AdM
    -----END PGP PUBLIC KEY BLOCK-----
    """
)


class TestAddTrustedKeys(TestCase):
    def test_add_trusted_keys(self):
        args = [
            "add-trusted-keys",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
        ]
        add_trusted_keys = parse_args(args=args).operation
        with tempfile.NamedTemporaryFile(mode="wb+") as keys_file:
            keys_file.write(TEST_GPG_KEY.encode())
            keys_file.seek(0)
            add_trusted_keys.input_file = keys_file
            with tempfile.NamedTemporaryFile(mode="wb+") as show_keys_file:
                add_trusted_keys.show_keys_file = show_keys_file
                self.assertEqual(0, add_trusted_keys.run())
                expected_dearmored_key = subprocess.run(
                    [
                        "gpg",
                        "--ignore-time-conflict",
                        "--no-options",
                        "--no-keyring",
                        "--dearmor",
                    ],
                    input=TEST_GPG_KEY.encode(),
                    capture_output=True,
                ).stdout
                self.assertEqual(
                    (expected_dearmored_key, stat.S_IFREG | 0o644),
                    add_trusted_keys.backend.backend_fs[
                        "/etc/apt/trusted.gpg.d/launchpad-buildd.gpg"
                    ],
                )
                show_keys_file.seek(0)
                self.assertIn(
                    "Key fingerprint = F6EC B376 2474 EDA9 D21B  "
                    "7022 8719 20D1 991B C93C",
                    show_keys_file.read().decode(),
                )


class RanAptGet(MatchesListwise):
    def __init__(self, args_list):
        super().__init__(
            [
                MatchesListwise(
                    [
                        Equals((["/usr/bin/apt-get"] + args,)),
                        ContainsDict(
                            {
                                "env": MatchesDict(
                                    {
                                        "LANG": Equals("C"),
                                        "DEBIAN_FRONTEND": Equals(
                                            "noninteractive"
                                        ),
                                        "TTY": Equals("unknown"),
                                    }
                                ),
                            }
                        ),
                    ]
                )
                for args in args_list
            ]
        )


class TestUpdate(TestCase):
    def test_succeeds(self):
        self.useFixture(FakeTime())
        start_time = time.time()
        args = [
            "update-debian-chroot",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
        ]
        update = parse_args(args=args).operation
        self.assertEqual(0, update.run())

        expected_args = [
            ["-uy", "update"],
            [
                "-o",
                "DPkg::Options::=--force-confold",
                "-uy",
                "--purge",
                "dist-upgrade",
            ],
        ]
        self.assertThat(update.backend.run.calls, RanAptGet(expected_args))
        self.assertEqual(start_time, time.time())

    def test_first_run_fails(self):
        class FailFirstTime(FakeMethod):
            def __call__(self, run_args, *args, **kwargs):
                super().__call__(run_args, *args, **kwargs)
                if len(self.calls) == 1:
                    raise subprocess.CalledProcessError(1, run_args)

        logger = self.useFixture(FakeLogger())
        self.useFixture(FakeTime())
        start_time = time.time()
        args = [
            "update-debian-chroot",
            "--backend=fake",
            "--series=xenial",
            "--arch=amd64",
            "1",
        ]
        update = parse_args(args=args).operation
        update.backend.run = FailFirstTime()
        self.assertEqual(0, update.run())

        expected_args = [
            ["-uy", "update"],
            ["-uy", "update"],
            [
                "-o",
                "DPkg::Options::=--force-confold",
                "-uy",
                "--purge",
                "dist-upgrade",
            ],
        ]
        self.assertThat(update.backend.run.calls, RanAptGet(expected_args))
        self.assertEqual(
            "Updating target for build 1\n"
            "Waiting 15 seconds and trying again ...\n",
            logger.output,
        )
        self.assertEqual(start_time + 15, time.time())
