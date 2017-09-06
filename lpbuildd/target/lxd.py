# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

from contextlib import closing
import io
import json
import os
import stat
import subprocess
import tarfile
import tempfile
from textwrap import dedent
import time

import netaddr
import pylxd
from pylxd.exceptions import LXDAPIException

from lpbuildd.target.backend import (
    Backend,
    BackendException,
    )
from lpbuildd.util import (
    set_personality,
    shell_escape,
    )


LXD_RUNNING = 103


fallback_hosts = dedent("""\
    127.0.0.1\tlocalhost
    ::1\tlocalhost ip6-localhost ip6-loopback
    fe00::0\tip6-localnet
    ff00::0\tip6-mcastprefix
    ff02::1\tip6-allnodes
    ff02::2\tip6-allrouters
    """)


policy_rc_d = dedent("""\
    #! /bin/sh
    while :; do
        case "$1" in
            -*) shift ;;
            systemd-udevd|systemd-udevd.service|udev|udev.service)
                exit 0 ;;
            snapd|snapd.socket|snapd.service)
                exit 0 ;;
            *)
                echo "Not running services in chroot."
                exit 101
                ;;
        esac
    done
    """)


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

    _client = None

    @property
    def client(self):
        if self._client is None:
            self._client = pylxd.Client()
        return self._client

    @property
    def lxc_arch(self):
        return self.arches[self.arch]

    @property
    def alias(self):
        return "lp-%s-%s" % (self.series, self.arch)

    @property
    def name(self):
        return self.alias

    def is_running(self):
        try:
            container = self.client.containers.get(self.name)
            return container.status_code == LXD_RUNNING
        except LXDAPIException:
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
        metadata_file = tarfile.TarInfo(name="metadata.yaml")
        metadata_file.size = len(metadata_yaml)
        target_tarball.addfile(metadata_file, io.BytesIO(metadata_yaml))

        # Mangle the chroot tarball into the form needed by LXD: when using
        # the combined metadata/rootfs form, the rootfs must be under
        # rootfs/ rather than under chroot-autobuild/.
        for entry in source_tarball:
            fileptr = None
            try:
                orig_name = entry.name.split("chroot-autobuild", 1)[-1]
                entry.name = "rootfs" + orig_name

                if entry.isfile():
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
        self.remove_image()

        # This is a lot of data to shuffle around in Python, but there
        # doesn't currently seem to be any way to ask pylxd to ask lxd to
        # import an image from a file on disk.
        with io.BytesIO() as target_file:
            with tarfile.open(name=tarball_path, mode="r") as source_tarball:
                with tarfile.open(
                        fileobj=target_file, mode="w") as target_tarball:
                    self._convert(source_tarball, target_tarball)

            image = self.client.images.create(
                target_file.getvalue(), wait=True)
            image.add_alias(self.alias, self.alias)

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
            ["sudo", "sysctl", "-q", "-w", "net.ipv4.ip_forward=1"])
        self.iptables(
            ["-t", "mangle", "-A", "FORWARD", "-i", self.bridge_name,
             "-p", "tcp", "--tcp-flags", "SYN,RST", "SYN",
             "-j", "TCPMSS", "--clamp-mss-to-pmtu"])
        self.iptables(
            ["-t", "nat", "-A", "POSTROUTING",
             "-s", str(self.ipv4_network), "!", "-d", str(self.ipv4_network),
             "-j", "MASQUERADE"])
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
        self.iptables(
            ["-t", "mangle", "-D", "FORWARD", "-i", self.bridge_name,
             "-p", "tcp", "--tcp-flags", "SYN,RST", "SYN",
             "-j", "TCPMSS", "--clamp-mss-to-pmtu"])
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

    def create_profile(self):
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

        try:
            old_profile = self.client.profiles.get(self.profile_name)
        except LXDAPIException:
            pass
        else:
            old_profile.delete()

        raw_lxc_config = [
            ("lxc.aa_profile", "unconfined"),
            ("lxc.cgroup.devices.deny", ""),
            ("lxc.cgroup.devices.allow", ""),
            ("lxc.mount.auto", ""),
            ("lxc.mount.auto", "proc:rw sys:rw"),
            ("lxc.network.0.ipv4", ipv4_address),
            ("lxc.network.0.ipv4.gateway", self.ipv4_network.ip),
            ]
        if self.arch == "powerpc":
            raw_lxc_config.append(("lxc.seccomp", ""))
        config = {
            "security.privileged": "true",
            "security.nesting": "true",
            "raw.lxc": "".join(
                "{key}={value}\n".format(key=key, value=value)
                for key, value in raw_lxc_config),
            }
        devices = {
            "eth0": {
                "name": "eth0",
                "nictype": "bridged",
                "parent": self.bridge_name,
                "type": "nic",
                },
            "loop-control": {
                "major": "10",
                "minor": "237",
                "path": "/dev/loop-control",
                "type": "unix-char",
                },
            }
        for minor in range(8):
            devices["loop%d" % minor] = {
                "major": "7",
                "minor": str(minor),
                "path": "/dev/loop%d" % minor,
                "type": "unix-block",
                }
        self.client.profiles.create(self.profile_name, config, devices)

    def start(self):
        """See `Backend`."""
        self.stop()

        self.create_profile()
        self.start_bridge()

        container = self.client.containers.create({
            "name": self.name,
            "profiles": [self.profile_name],
            "source": {"type": "image", "alias": self.alias},
            }, wait=True)

        with tempfile.NamedTemporaryFile(mode="w+b") as hosts_file:
            try:
                self.copy_out("/etc/hosts", hosts_file.name)
            except LXDAPIException:
                hosts_file.seek(0, os.SEEK_SET)
                hosts_file.write(fallback_hosts.encode("UTF-8"))
            hosts_file.seek(0, os.SEEK_END)
            print("\n127.0.1.1\t%s" % self.name, file=hosts_file)
            hosts_file.flush()
            os.fchmod(hosts_file.fileno(), 0o644)
            self.copy_in(hosts_file.name, "/etc/hosts")
        with tempfile.NamedTemporaryFile(mode="w+") as hostname_file:
            print(self.name, file=hostname_file)
            hostname_file.flush()
            os.fchmod(hostname_file.fileno(), 0o644)
            self.copy_in(hostname_file.name, "/etc/hostname")
        self.copy_in("/etc/resolv.conf", "/etc/resolv.conf")
        with tempfile.NamedTemporaryFile(mode="w+") as policy_rc_d_file:
            policy_rc_d_file.write(policy_rc_d)
            policy_rc_d_file.flush()
            os.fchmod(policy_rc_d_file.fileno(), 0o755)
            self.copy_in(policy_rc_d_file.name, "/usr/local/sbin/policy-rc.d")

        # Start the container and wait for it to start.
        container.start(wait=True)
        timeout = 60
        now = time.time()
        while time.time() < now + timeout:
            try:
                container = self.client.containers.get(self.name)
            except LXDAPIException:
                container = None
                break
            if container.status_code == LXD_RUNNING:
                break
            time.sleep(1)
        if container is None or container.status_code != LXD_RUNNING:
            raise BackendException(
                "Container failed to start within %d seconds" % timeout)

    def run(self, args, env=None, input_text=None, get_output=False,
            echo=False, **kwargs):
        """See `Backend`."""
        env_params = []
        if env:
            for key, value in env.items():
                env_params.extend(["--env", "%s=%s" % (key, value)])
        if self.arch is not None:
            args = set_personality(args, self.arch, series=self.series)
        if echo:
            print("Running in container: %s" % ' '.join(
                shell_escape(arg) for arg in args))
        # pylxd's Container.execute doesn't support sending stdin, and it's
        # tedious to implement ourselves.
        cmd = ["lxc", "exec", self.name] + env_params + ["--"] + args
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
        # pylxd's FilesManager doesn't support sending UID/GID/mode.
        container = self.client.containers.get(self.name)
        with open(source_path, "rb") as source_file:
            params = {"path": target_path}
            data = source_file.read()
            mode = stat.S_IMODE(os.fstat(source_file.fileno()).st_mode)
            headers = {
                "X-LXD-uid": 0,
                "X-LXD-gid": 0,
                "X-LXD-mode": "%#o" % mode,
                }
            container.api.files.post(params=params, data=data, headers=headers)

    def _get_file(self, container, *args, **kwargs):
        # pylxd < 2.1.1 tries to validate the response as JSON in streaming
        # mode and ends up running out of memory on large files.  Work
        # around this.
        response = container.api.files.session.get(
            container.api.files._api_endpoint, *args, **kwargs)
        if response.status_code != 200:
            raise LXDAPIException(response)
        return response

    def copy_out(self, source_path, target_path):
        # pylxd's FilesManager doesn't support streaming, which is important
        # since copied-out files may be large.
        # This ignores UID/GID/mode, but then so does "lxc file pull".
        container = self.client.containers.get(self.name)
        with open(target_path, "wb") as target_file:
            params = {"path": source_path}
            with closing(
                    self._get_file(
                        container, params=params, stream=True)) as response:
                for chunk in response.iter_content(chunk_size=65536):
                    target_file.write(chunk)

    def stop(self):
        """See `Backend`."""
        try:
            container = self.client.containers.get(self.name)
        except LXDAPIException:
            pass
        else:
            if container.status_code == LXD_RUNNING:
                container.stop(wait=True)
            container.delete(wait=True)
        self.stop_bridge()

    def remove_image(self):
        for image in self.client.images.all():
            if any(alias["name"] == self.alias for alias in image.aliases):
                image.delete(wait=True)
                return

    def remove(self):
        """See `Backend`."""
        self.remove_image()
        super(LXD, self).remove()
