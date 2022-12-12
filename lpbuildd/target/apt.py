# Copyright 2009-2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import logging
import os
import subprocess
import sys
from textwrap import dedent
import time

from lpbuildd.target.operation import Operation


logger = logging.getLogger(__name__)


class OverrideSourcesList(Operation):

    description = "Override sources.list in the target environment."

    @classmethod
    def add_arguments(cls, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--apt-proxy-url", metavar="URL", help="APT proxy URL")
        parser.add_argument(
            "archives", metavar="ARCHIVE", nargs="+",
            help="sources.list lines")

    def run(self):
        logger.info("Overriding sources.list in build-%s", self.args.build_id)
        with self.backend.open(
            "/etc/apt/sources.list", mode="w+"
        ) as sources_list:
            for archive in self.args.archives:
                print(archive, file=sources_list)
            os.fchmod(sources_list.fileno(), 0o644)
        with self.backend.open(
            "/etc/apt/apt.conf.d/99retries", mode="w+"
        ) as apt_retries_conf:
            print('Acquire::Retries "3";', file=apt_retries_conf)
            os.fchmod(apt_retries_conf.fileno(), 0o644)
        # Versions of APT that support phased updates do this automatically
        # if running in a chroot, but builds may be running in a LXD
        # container instead.
        with self.backend.open(
            "/etc/apt/apt.conf.d/99phasing", mode="w+"
        ) as apt_phasing_conf:
            print('APT::Get::Always-Include-Phased-Updates "true";',
                  file=apt_phasing_conf)
            os.fchmod(apt_phasing_conf.fileno(), 0o644)
        if self.args.apt_proxy_url is not None:
            with self.backend.open(
                "/etc/apt/apt.conf.d/99proxy", mode="w+"
            ) as apt_proxy_conf:
                print(
                    f'Acquire::http::Proxy "{self.args.apt_proxy_url}";',
                    file=apt_proxy_conf)
                os.fchmod(apt_proxy_conf.fileno(), 0o644)
        for pocket in ("proposed", "backports"):
            with self.backend.open(
                f"/etc/apt/preferences.d/{pocket}.pref", mode="w+"
            ) as preferences:
                print(dedent(f"""\
                    Package: *
                    Pin: release a=*-{pocket}
                    Pin-Priority: 500
                    """), file=preferences, end="")
                os.fchmod(preferences.fileno(), 0o644)
        return 0


class AddTrustedKeys(Operation):

    description = "Write out new trusted keys."

    def __init__(self, args, parser):
        super().__init__(args, parser)
        self.input_file = sys.stdin.buffer
        self.show_keys_file = sys.stdout.buffer

    def run(self):
        """Add trusted keys from an input file."""
        logger.info("Adding trusted keys to build-%s", self.args.build_id)
        # We must read the input data before calling `backend.open`, since
        # it may call `lxc exec` and that apparently drains stdin.
        input_data = self.input_file.read()
        gpg_cmd = [
            "gpg", "--ignore-time-conflict", "--no-options", "--no-keyring",
            ]
        with self.backend.open(
            "/etc/apt/trusted.gpg.d/launchpad-buildd.gpg", mode="wb+"
        ) as keyring:
            subprocess.run(
                gpg_cmd + ["--dearmor"], input=input_data, stdout=keyring,
                check=True)
            keyring.seek(0)
            subprocess.check_call(
                gpg_cmd +
                ["--show-keys", "--keyid-format", "long", "--fingerprint"],
                stdin=keyring, stdout=self.show_keys_file)
            os.fchmod(keyring.fileno(), 0o644)
        return 0


class Update(Operation):

    description = "Update the target environment."

    def run(self):
        logger.info("Updating target for build %s", self.args.build_id)
        with open("/dev/null") as devnull:
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
