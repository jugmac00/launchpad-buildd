# Copyright 2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import os.path
import re
import shutil
import subprocess

from charmhelpers import fetch
from charmhelpers.core import (
    hookenv,
    host,
    )
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
    set_state("launchpad-buildd.needs_install")


@hook("upgrade-charm", "config-changed")
def mark_needs_install():
    set_state("launchpad-buildd.needs_install")


@when_not("apt.needs_update")
@when("launchpad-buildd.needs_install")
def install_packages():
    cache_dir = os.path.join(hookenv.charm_dir(), "cache")
    host.mkdir(cache_dir, perms=0o755)
    to_install = []
    packages = ["launchpad-buildd", "python3-lpbuildd"]
    options = ["--option=Dpkg::Options::=--force-confold"]
    resource_paths = [hookenv.resource_get(package) for package in packages]
    if all(path and os.path.getsize(path) for path in resource_paths):
        # Install from resources.
        changed = False
        for package, resource_path in zip(packages, resource_paths):
            local_path = os.path.join(cache_dir, f"{package}.deb")
            to_install.append((local_path, resource_path))
            if host.file_hash(local_path) != host.file_hash(resource_path):
                changed = True
        if not changed:
            return
        options.append("--reinstall")
    else:
        # We don't have resource-provided packages, so just install from the
        # PPA.
        to_install.extend([(None, package) for package in packages])
    new_paths = [new_path for _, new_path in to_install]
    try:
        status_set(None, f"Installing {','.join(packages)}")
        fetch.apt_unhold(packages)
        fetch.apt_install(new_paths, options=options)
        fetch.apt_hold(packages)
    except subprocess.CalledProcessError:
        status_set(
            "blocked", f"Unable to install packages {','.join(packages)}")
    else:
        for local_path, resource_path in to_install:
            if local_path is not None:
                shutil.copy2(resource_path, local_path)
        # ntp.buildd isn't likely to work outside of the Canonical
        # datacentre, and LXD containers can't set the system time.  Let's
        # just not worry about NTP.
        config_path = "/etc/launchpad-buildd/default"
        with open(config_path) as config_file:
            config = config_file.read()
        config = re.sub(r"^ntphost = .*", "ntphost = ", config, flags=re.M)
        with open(config_path + ".new", "w") as new_config_file:
            new_config_file.write(config)
        os.rename(config_path + ".new", config_path)
        remove_state("launchpad-buildd.needs_install")
        set_state("launchpad-buildd.installed")


@when("apt.installed.bzr-builder")
@when("apt.installed.git-build-recipe")
@when("apt.installed.quilt")
@when("launchpad-buildd.installed")
def mark_active():
    status_set("active", "Builder running")
