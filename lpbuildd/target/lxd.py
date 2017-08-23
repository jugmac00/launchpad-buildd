# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
from textwrap import dedent
import time

import netaddr

from lpbuildd.target.backend import (
    Backend,
    BackendException,
    )
from lpbuildd.util import (
    set_personality,
    shell_escape,
    )


class LXD(Backend):

    # Architecture mapping
    arches = {
        "amd64": "x86_64",
        "arm64": "aarch64",
        "armhf": "armv7l",
        "i386": "i686",
        "powerpc": "ppc",
        "ppc64el": "ppc64le",
        "s390x": "s390x",
        }

    profile_name = "lpbuildd"
    bridge_name = "lpbuilddbr0"
    # XXX cjwatson 2017-08-07: Hardcoded for now to be in a range reserved
    # for employee private networks in
    # https://wiki.canonical.com/InformationInfrastructure/IS/Network, so it
    # won't collide with any production networks.  This should be
    # configurable.
    ipv4_network = netaddr.IPNetwork("10.10.10.1/24")
    run_dir = "/run/launchpad-buildd"

    @property
    def lxc_arch(self):
        return self.arches[self.arch]

    @property
    def alias(self):
        return "lp-%s-%s" % (self.series, self.arch)

    @property
    def name(self):
        return self.alias

    def profile_exists(self):
        with open("/dev/null", "w") as devnull:
            return subprocess.call(
                ["sudo", "lxc", "profile", "show", self.profile_name],
                stdout=devnull, stderr=devnull) == 0

    def image_exists(self):
        with open("/dev/null", "w") as devnull:
            return subprocess.call(
                ["sudo", "lxc", "image", "info", self.alias],
                stdout=devnull, stderr=devnull) == 0

    def container_exists(self):
        with open("/dev/null", "w") as devnull:
            return subprocess.call(
                ["sudo", "lxc", "info", self.name],
                stdout=devnull, stderr=devnull) == 0

    def is_running(self):
        try:
            with open("/dev/null", "w") as devnull:
                output = subprocess.check_output(
                    ["sudo", "lxc", "info", self.name], stderr=devnull)
            for line in output.splitlines():
                if line.strip() == "Status: Running":
                    return True
            else:
                return False
        except Exception:
            return False

    def _convert(self, source_tarball, target_tarball):
        creation_time = source_tarball.getmember("chroot-autobuild").mtime
        metadata = {
            "architecture": self.lxc_arch,
            "creation_date": creation_time,
            "properties": {
                "os": "Ubuntu",
                "series": self.series,
                "architecture": self.arch,
                "description": "Launchpad chroot for Ubuntu %s (%s)" % (
                    self.series, self.arch),
                },
            }
        # Encoding this as JSON is good enough, and saves pulling in a YAML
        # library dependency.
        metadata_yaml = json.dumps(
            metadata, sort_keys=True, indent=4, separators=(",", ": "),
            ensure_ascii=False).encode("UTF-8") + b"\n"
        metadata_file = tarfile.TarInfo()
        metadata_file.size = len(metadata_yaml)
        metadata_file.name = "metadata.yaml"
        target_tarball.addfile(metadata_file, io.BytesIO(metadata_yaml))

        copy_from_host = {"/etc/hosts", "/etc/hostname", "/etc/resolv.conf"}

        for entry in source_tarball:
            fileptr = None
            try:
                orig_name = entry.name.split("chroot-autobuild", 1)[-1]
                entry.name = "rootfs" + orig_name

                if entry.isfile():
                    if orig_name in copy_from_host:
                        target_tarball.add(
                            os.path.realpath(orig_name), arcname=entry.name)
                        continue
                    elif orig_name == "/usr/local/sbin/policy-rc.d":
                        new_bytes = dedent("""\
                            #! /bin/sh
                            while :; do
                                case "$1" in
                                    -*)             shift ;;
                                    snapd.service)  exit 0 ;;
                                    *)
                                        echo "Not running services in chroot."
                                        exit 101
                                        ;;
                                esac
                            done
                            """).encode("UTF-8")
                        entry.size = len(new_bytes)
                        fileptr = io.BytesIO(new_bytes)
                    else:
                        try:
                            fileptr = source_tarball.extractfile(entry.name)
                        except KeyError:
                            pass
                elif entry.islnk():
                    # Update hardlinks to point to the right target
                    entry.linkname = (
                        "rootfs" +
                        entry.linkname.split("chroot-autobuild", 1)[-1])

                target_tarball.addfile(entry, fileobj=fileptr)
            finally:
                if fileptr is not None:
                    fileptr.close()

    def create(self, tarball_path):
        """See `Backend`."""
        if self.image_exists():
            self.remove_image()

        tempdir = tempfile.mkdtemp()
        try:
            target_path = os.path.join(tempdir, "lxd.tar.gz")
            with tarfile.open(tarball_path, "r") as source_tarball:
                with tarfile.open(target_path, "w:gz") as target_tarball:
                    self._convert(source_tarball, target_tarball)

            with open("/dev/null", "w") as devnull:
                subprocess.check_call(
                    ["sudo", "lxc", "image", "import", target_path,
                     "--alias", self.alias], stdout=devnull)
        finally:
            shutil.rmtree(tempdir)

    @property
    def sys_dir(self):
        return os.path.join("/sys/class/net", self.bridge_name)

    @property
    def dnsmasq_pid_file(self):
        return os.path.join(self.run_dir, "dnsmasq.pid")

    def iptables(self, args, check=True):
        call = subprocess.check_call if check else subprocess.call
        call(
            ["sudo", "iptables", "-w"] + args +
            ["-m", "comment", "--comment", "managed by launchpad-buildd"])

    def start_bridge(self):
        if not os.path.isdir(self.run_dir):
            os.makedirs(self.run_dir)
        subprocess.check_call(
            ["sudo", "ip", "link", "add", "dev", self.bridge_name,
             "type", "bridge"])
        subprocess.check_call(
            ["sudo", "ip", "addr", "add", str(self.ipv4_network),
             "dev", self.bridge_name])
        subprocess.check_call(
            ["sudo", "ip", "link", "set", "dev", self.bridge_name, "up"])
        subprocess.check_call(
            ["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"])
        self.iptables(
            ["-t", "nat", "-A", "POSTROUTING",
             "-s", str(self.ipv4_network), "!", "-d", str(self.ipv4_network),
             "-j", "MASQUERADE"])
        for protocol in ("udp", "tcp"):
            self.iptables(
                ["-I", "INPUT", "-i", self.bridge_name,
                 "-p", protocol, "--dport", "53", "-j", "ACCEPT"])
        self.iptables(
            ["-I", "FORWARD", "-i", self.bridge_name, "-j", "ACCEPT"])
        self.iptables(
            ["-I", "FORWARD", "-o", self.bridge_name, "-j", "ACCEPT"])
        subprocess.check_call(
            ["sudo", "/usr/sbin/dnsmasq", "-s", "lpbuildd", "-S", "/lpbuildd/",
             "-u", "buildd", "--strict-order", "--bind-interfaces",
             "--pid-file=%s" % self.dnsmasq_pid_file,
             "--except-interface=lo", "--interface=%s" % self.bridge_name,
             "--listen-address=%s" % str(self.ipv4_network.ip)])

    def stop_bridge(self):
        if not os.path.isdir(self.sys_dir):
            return
        subprocess.call(
            ["sudo", "ip", "addr", "flush", "dev", self.bridge_name])
        subprocess.call(
            ["sudo", "ip", "link", "set", "dev", self.bridge_name, "down"])
        for protocol in ("udp", "tcp"):
            self.iptables(
                ["-D", "INPUT", "-i", self.bridge_name,
                 "-p", protocol, "--dport", "53", "-j", "ACCEPT"], check=False)
        self.iptables(
            ["-D", "FORWARD", "-i", self.bridge_name, "-j", "ACCEPT"],
            check=False)
        self.iptables(
            ["-D", "FORWARD", "-o", self.bridge_name, "-j", "ACCEPT"],
            check=False)
        self.iptables(
            ["-t", "nat", "-D", "POSTROUTING",
             "-s", str(self.ipv4_network), "!", "-d", str(self.ipv4_network),
             "-j", "MASQUERADE"], check=False)
        if os.path.exists(self.dnsmasq_pid_file):
            with open(self.dnsmasq_pid_file) as f:
                try:
                    dnsmasq_pid = int(f.read())
                except Exception:
                    pass
                else:
                    # dnsmasq is supposed to drop privileges, but kill it as
                    # root just in case it fails to do so for some reason.
                    subprocess.call(["sudo", "kill", "-9", str(dnsmasq_pid)])
            os.unlink(self.dnsmasq_pid_file)
        subprocess.call(["sudo", "ip", "link", "delete", self.bridge_name])

    def start(self):
        """See `Backend`."""
        self.stop()

        for addr in self.ipv4_network:
            if addr not in (
                    self.ipv4_network.network, self.ipv4_network.ip,
                    self.ipv4_network.broadcast):
                ipv4_address = netaddr.IPNetwork(
                    (int(addr), self.ipv4_network.prefixlen))
                break
        else:
            raise BackendException(
                "%s has no usable IP addresses" % self.ipv4_network)

        if self.profile_exists():
            with open("/dev/null", "w") as devnull:
                subprocess.check_call(
                    ["sudo", "lxc", "profile", "delete", self.profile_name],
                    stdout=devnull)
        subprocess.check_call(
            ["sudo", "lxc", "profile", "copy", "default", self.profile_name])
        subprocess.check_call(
            ["sudo", "lxc", "profile", "device", "set", self.profile_name,
             "eth0", "parent", self.bridge_name])

        def set_key(key, value):
            subprocess.check_call(
                ["sudo", "lxc", "profile", "set", self.profile_name,
                 key, value])

        set_key("security.privileged", "true")
        set_key("security.nesting", "true")
        set_key("raw.lxc", dedent("""\
            lxc.aa_profile=unconfined
            lxc.cgroup.devices.deny=
            lxc.cgroup.devices.allow=
            lxc.network.0.ipv4={ipv4_address}
            lxc.network.0.ipv4.gateway={ipv4_gateway}
            """.format(
                ipv4_address=ipv4_address, ipv4_gateway=self.ipv4_network.ip)))

        self.start_bridge()

        subprocess.check_call(
            ["sudo", "lxc", "init", "--ephemeral", "-p", self.profile_name,
             self.alias, self.name])

        for path in ("/etc/hosts", "/etc/hostname", "/etc/resolv.conf"):
            self.copy_in(path, path)

        # Start the container
        with open("/dev/null", "w") as devnull:
            subprocess.check_call(
                ["sudo", "lxc", "start", self.name], stdout=devnull)

        # Wait for container to start
        timeout = 60
        now = time.time()
        while time.time() < now + timeout:
            if self.is_running():
                return
            time.sleep(5)
        if not self.is_running():
            raise BackendException(
                "Container failed to start within %d seconds" % timeout)

    def run(self, args, env=None, input_text=None, get_output=False,
            echo=False, **kwargs):
        """See `Backend`."""
        if env:
            args = ["env"] + [
                "%s=%s" % (key, shell_escape(value))
                for key, value in env.items()] + args
        if self.arch is not None:
            args = set_personality(args, self.arch, series=self.series)
        if echo:
            print("Running in container: %s" % ' '.join(
                shell_escape(arg) for arg in args))
        cmd = ["sudo", "lxc", "exec", self.name, "--"] + args
        if input_text is None and not get_output:
            subprocess.check_call(cmd, **kwargs)
        else:
            if get_output:
                kwargs["stdout"] = subprocess.PIPE
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, universal_newlines=True, **kwargs)
            output, _ = proc.communicate(input_text)
            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, cmd)
            if get_output:
                return output

    def copy_in(self, source_path, target_path):
        """See `Backend`."""
        mode = stat.S_IMODE(os.stat(source_path).st_mode)
        subprocess.check_call(
            ["sudo", "lxc", "file", "push",
             "--uid=0", "--gid=0", "--mode=%o" % mode,
             source_path, self.name + target_path])

    def copy_out(self, source_path, target_path):
        subprocess.check_call(
            ["sudo", "lxc", "file", "pull",
             self.name + source_path, target_path])

    def stop(self):
        """See `Backend`."""
        if self.is_running():
            subprocess.check_call(["sudo", "lxc", "stop", self.name])
        if self.container_exists():
            subprocess.check_call(["sudo", "lxc", "delete", self.name])
        self.stop_bridge()

    def remove_image(self):
        subprocess.check_call(["sudo", "lxc", "image", "delete", self.alias])

    def remove(self):
        """See `Backend`."""
        if self.image_exists():
            self.remove_image()
        super(LXD, self).remove()
