# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__metaclass__ = type

import argparse


class SudoUmount:

    name = "sudo"

    def __init__(self, delays=None):
        self.delays = delays or {}

    def __call__(self, proc_args):
        parser = argparse.ArgumentParser()
        parser.add_argument("command", choices=["umount"])
        parser.add_argument("mount_path")
        args = parser.parse_args(proc_args["args"][1:])
        if self.delays.get(args.mount_path, 0) > 0:
            self.delays[args.mount_path] -= 1
            return {'returncode': 1}
        with open("/proc/mounts") as mounts_file:
            mounts = mounts_file.readlines()
        to_remove = None
        for i, mount in reversed(list(enumerate(mounts))):
            if mount.split()[1] == args.mount_path:
                to_remove = i
                break
        if to_remove is None:
            return {'returncode': 1}
        else:
            del mounts[to_remove]
            with open("/proc/mounts", "w") as mounts_file:
                for mount in mounts:
                    mounts_file.write(mount)
            return {}
