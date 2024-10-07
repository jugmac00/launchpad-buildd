# Copyright 2009-2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import logging
import os
import re
import subprocess
import sys
import time
from textwrap import dedent

from lpbuildd.target.operation import Operation

logger = logging.getLogger(__name__)


class OverrideSourcesList(Operation):
    description = "Override sources.list in the target environment."

    @classmethod
    def add_arguments(cls, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--apt-proxy-url", metavar="URL", help="APT proxy URL"
        )
        parser.add_argument(
            "archives", metavar="ARCHIVE", nargs="+", help="sources.list lines"
        )

    def run(self):
        logger.info("Overriding sources.list in build-%s", self.args.build_id)
        # If the ubuntu version is < 24.04 then use the old one line format
        # for backward compatibility.
        if self.backend.series in [
            "trusty",
            "xenial",
            "bionic",
            "focal",
            "jammy",
        ]:
            with self.backend.open(
                "/etc/apt/sources.list", mode="w+"
            ) as sources_list:
                for archive in self.args.archives:
                    print(archive, file=sources_list)
                os.fchmod(sources_list.fileno(), 0o644)
        # If the ubuntu version is >= 24.04 then use deb822 format
        else:
            self.backend.run(
                ["rm", "-f", "/etc/apt/sources.list.d/ubuntu.sources"]
            )
            self.backend.run(["rm", "-f", "/etc/apt/sources.list"])
            with self.backend.open(
                "/etc/apt/sources.list.d/lp-buildd.sources", mode="w+"
            ) as sources_list:
                for archive in self.args.archives:
                    source = self._prepare_source(archive)
                    if len(source) == 0:
                        logger.error("Error parsing source: %s", archive)
                        continue
                    for key, value in source.items():
                        if isinstance(value, str):
                            sources_list.write(f"{key}: {value}\n")
                        else:
                            sources_list.write(
                                "{}: {}\n".format(key, " ".join(value))
                            )
                    sources_list.write("\n")
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
            print(
                'APT::Get::Always-Include-Phased-Updates "true";',
                file=apt_phasing_conf,
            )
            os.fchmod(apt_phasing_conf.fileno(), 0o644)
        if self.args.apt_proxy_url is not None:
            with self.backend.open(
                "/etc/apt/apt.conf.d/99proxy", mode="w+"
            ) as apt_proxy_conf:
                print(
                    f'Acquire::http::Proxy "{self.args.apt_proxy_url}";',
                    file=apt_proxy_conf,
                )
                os.fchmod(apt_proxy_conf.fileno(), 0o644)
        for pocket in ("proposed", "backports"):
            with self.backend.open(
                f"/etc/apt/preferences.d/{pocket}.pref", mode="w+"
            ) as preferences:
                print(
                    dedent(
                        f"""\
                    Package: *
                    Pin: release a=*-{pocket}
                    Pin-Priority: 500
                    """
                    ),
                    file=preferences,
                    end="",
                )
                os.fchmod(preferences.fileno(), 0o644)
        return 0

    def _split_options(self, raw):
        table = str.maketrans({"[": None, "]": None})
        options = raw.translate(table).split(" ")

        return options

    def _prepare_source(self, line):
        pattern = re.compile(
            r"^(?: *(?P<type>deb|deb-src)) +"
            r"(?P<options>\[.+\] ?)*"
            r"(?P<uri>\w+:\/\/\S+) +"
            r"(?P<suite>\S+)"
            r"(?: +(?P<components>.*))?$"
        )

        old_to_deb822 = {
            "arch": "Architectures",
            "signed-by": "Signed-By",
            "lang": "Languages",
            "target": "Targets",
            "trusted": "Trusted",
            "by-hash": "By-Hash",
            "pdiffs": "PDiffs",
            "allow-insecure": "Allow-Insecure",
            "allow-weak": "Allow-Weak",
            "allow-downgrade-to-insecure": "Allow-Downgrade-To-Insecure",
            "snapshot": "Snapshot",
            "inrelease-path": "InRelease-Path",
            "check-valid-until": "Check-Valid-Until",
            "valid-until-min": "Valid-Until-Min",
            "valid-until-max": "Valid-Until-Max",
            "check-date": "Check-Date",
            "date-max-future": "Date-Max-Future",
        }

        matches = re.match(pattern, line)
        source = {}
        if matches is not None:
            options = {}
            if matches.group("options"):
                for option in self._split_options(matches["options"]):
                    if "=" in option:
                        key, value = option.split("=")
                        options[key] = value
            source = {
                "Types": {matches["type"]},
                "URIs": matches["uri"],
                "Enabled": "yes",
            }
            if matches.group("suite"):
                source["Suites"] = set(matches["suite"].split(" "))
            if matches.group("components"):
                source["Components"] = set(matches["components"].split(" "))
            for key in options.keys():
                if key in old_to_deb822:
                    if old_to_deb822[key] in source:
                        source[old_to_deb822[key]].append(options[key])
                    else:
                        source[old_to_deb822[key]] = {options[key]}
                else:
                    # reject the source
                    logger.error("Unknown option: %s", key)
                    return {}
        return source


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
            "gpg",
            "--ignore-time-conflict",
            "--no-options",
            "--no-keyring",
        ]
        with self.backend.open(
            "/etc/apt/trusted.gpg.d/launchpad-buildd.gpg", mode="wb+"
        ) as keyring:
            subprocess.run(
                gpg_cmd + ["--dearmor"],
                input=input_data,
                stdout=keyring,
                check=True,
            )
            keyring.seek(0)
            subprocess.check_call(
                gpg_cmd
                + ["--show-keys", "--keyid-format", "long", "--fingerprint"],
                stdin=keyring,
                stdout=self.show_keys_file,
            )
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
                apt_get,
                "-o",
                "DPkg::Options::=--force-confold",
                "-uy",
                "--purge",
                "dist-upgrade",
            ]
            self.backend.run(upgrade_args, env=env, stdin=devnull)
        return 0
