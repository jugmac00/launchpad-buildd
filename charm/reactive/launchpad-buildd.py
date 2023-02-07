# Copyright 2016-2022 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import os.path
import re

from charms.apt import status_set
from charms.reactive import (
    hook,
    only_once,
    remove_state,
    set_state,
    when,
    when_not,
)


@only_once
def install():
    with open("/etc/default/launchpad-buildd", "w") as default_file:
        print("RUN_NETWORK_REQUESTS_AS_ROOT=yes", file=default_file)
    remove_state("launchpad-buildd.installed")


@hook("upgrade-charm", "config-changed")
def mark_needs_install():
    remove_state("launchpad-buildd.installed")


@when("apt.installed.launchpad-buildd")
@when_not("launchpad-buildd.installed")
def configure_launchpad_buildd():
    # ntp.buildd isn't likely to work outside of the Canonical datacentre,
    # and LXD containers can't set the system time.  Let's just not worry
    # about NTP.
    config_path = "/etc/launchpad-buildd/default"
    with open(config_path) as config_file:
        config = config_file.read()
    config = re.sub(r"^ntphost = .*", "ntphost = ", config, flags=re.M)
    with open(config_path + ".new", "w") as new_config_file:
        new_config_file.write(config)
    os.rename(config_path + ".new", config_path)
    set_state("launchpad-buildd.installed")


@when("apt.installed.bzr-builder")
@when("apt.installed.git-build-recipe")
@when("apt.installed.quilt")
@when("launchpad-buildd.installed")
def mark_active():
    status_set("active", "Builder running")
