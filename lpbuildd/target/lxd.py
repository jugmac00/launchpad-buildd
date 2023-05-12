# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import io
import json
import os
import re
import stat
import subprocess
import tarfile
import time
from contextlib import closing
from functools import cached_property
from textwrap import dedent

import netaddr
import pylxd
from pylxd.exceptions import LXDAPIException

from lpbuildd.target.backend import Backend, BackendException
from lpbuildd.util import set_personality, shell_escape

LXD_RUNNING = 103


def get_device_mapper_major():
    """Return the major device number used by the devicemapper on this system.

    This is not consistent across kernel versions, sadly.
    """
    with open("/proc/devices") as devices:
        for line in devices:
            if line.rstrip("\n").endswith(" device-mapper"):
                return int(line.split()[0])
        else:
            raise Exception(
                "Cannot determine major device number for device-mapper"
            )


fallback_hosts = dedent(
    """\
    127.0.0.1\tlocalhost
    ::1\tlocalhost ip6-localhost ip6-loopback
    fe00::0\tip6-localnet
    ff00::0\tip6-mcastprefix
    ff02::1\tip6-allnodes
    ff02::2\tip6-allrouters
    """
)


policy_rc_d = dedent(
    """\
    #! /bin/sh
    while :; do
        case "$1" in
            -*) shift ;;
            systemd-udevd|systemd-udevd.service|udev|udev.service)
                exit 0 ;;
            snapd|snapd.*)
                exit 0 ;;
            *)
                echo "Not running services in chroot."
                exit 101
                ;;
        esac
    done
    """
)


class LXDException(Exception):
    """Wrap an LXDAPIException with some more useful information."""

    def __init__(self, action, lxdapi_exc):
        self.action = action
        self.lxdapi_exc = lxdapi_exc

    def __str__(self):
        return f"{self.action}: {self.lxdapi_exc}"


class LXD(Backend):
    supports_snapd = True

    # Architecture mapping
    arches = {
        "amd64": "x86_64",
        "arm64": "aarch64",
        "armhf": "armv7l",
        "i386": "i686",
        "powerpc": "ppc",
        "ppc64el": "ppc64le",
        "riscv64": "riscv64",
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
        return f"lp-{self.series}-{self.arch}"

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
                "description": (
                    f"Launchpad chroot for Ubuntu {self.series} ({self.arch})"
                ),
            },
        }
        # Encoding this as JSON is good enough, and saves pulling in a YAML
        # library dependency.
        metadata_yaml = (
            json.dumps(
                metadata,
                sort_keys=True,
                indent=4,
                separators=(",", ": "),
                ensure_ascii=False,
            ).encode("UTF-8")
            + b"\n"
        )
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
                        "rootfs"
                        + entry.linkname.split("chroot-autobuild", 1)[-1]
                    )

                target_tarball.addfile(entry, fileobj=fileptr)
            finally:
                if fileptr is not None:
                    fileptr.close()

    def _init(self):
        """Configure LXD if necessary."""
        # "lxd init" creates a key pair (see
        # https://linuxcontainers.org/lxd/docs/master/authentication/), so
        # check for that to see whether LXD has already been initialized.
        if not os.path.exists("/var/snap/lxd/common/lxd/server.key"):
            subprocess.check_call(["sudo", "lxd", "init", "--auto"])
            # Generate a LXD client certificate for the buildd user.
            with open("/dev/null", "w") as devnull:
                subprocess.call(["lxc", "list"], stdout=devnull)

    def create(self, image_path, image_type):
        """See `Backend`."""
        self._init()
        self.remove_image()

        # This is a lot of data to shuffle around in Python, but there
        # doesn't currently seem to be any way to ask pylxd to ask lxd to
        # import an image from a file on disk.
        if image_type == "chroot":
            with io.BytesIO() as target_file:
                with tarfile.open(name=image_path, mode="r") as source_tarball:
                    with tarfile.open(
                        fileobj=target_file, mode="w"
                    ) as target_tarball:
                        self._convert(source_tarball, target_tarball)

                image = self.client.images.create(
                    target_file.getvalue(), wait=True
                )
        elif image_type == "lxd":
            with open(image_path, "rb") as image_file:
                image = self.client.images.create(image_file.read(), wait=True)
        else:
            raise ValueError("Unhandled image type: %s" % image_type)

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
            ["sudo", "iptables", "-w"]
            + args
            + ["-m", "comment", "--comment", "managed by launchpad-buildd"]
        )

    def start_bridge(self):
        if not os.path.isdir(self.run_dir):
            os.makedirs(self.run_dir)
        subprocess.check_call(
            [
                "sudo",
                "ip",
                "link",
                "add",
                "dev",
                self.bridge_name,
                "type",
                "bridge",
            ]
        )
        subprocess.check_call(
            [
                "sudo",
                "ip",
                "addr",
                "add",
                str(self.ipv4_network),
                "dev",
                self.bridge_name,
            ]
        )
        subprocess.check_call(
            ["sudo", "ip", "link", "set", "dev", self.bridge_name, "up"]
        )
        subprocess.check_call(
            ["sudo", "sysctl", "-q", "-w", "net.ipv4.ip_forward=1"]
        )
        self.iptables(
            [
                "-t",
                "mangle",
                "-A",
                "FORWARD",
                "-i",
                self.bridge_name,
                "-p",
                "tcp",
                "--tcp-flags",
                "SYN,RST",
                "SYN",
                "-j",
                "TCPMSS",
                "--clamp-mss-to-pmtu",
            ]
        )
        self.iptables(
            [
                "-t",
                "nat",
                "-A",
                "POSTROUTING",
                "-s",
                str(self.ipv4_network),
                "!",
                "-d",
                str(self.ipv4_network),
                "-j",
                "MASQUERADE",
            ]
        )
        subprocess.check_call(
            [
                "sudo",
                "/usr/sbin/dnsmasq",
                "-s",
                "lpbuildd",
                "-S",
                "/lpbuildd/",
                "-u",
                "buildd",
                "--strict-order",
                "--bind-interfaces",
                "--pid-file=%s" % self.dnsmasq_pid_file,
                "--except-interface=lo",
                "--interface=%s" % self.bridge_name,
                "--listen-address=%s" % str(self.ipv4_network.ip),
            ]
        )

    def stop_bridge(self):
        if not os.path.isdir(self.sys_dir):
            return
        subprocess.call(
            ["sudo", "ip", "addr", "flush", "dev", self.bridge_name]
        )
        subprocess.call(
            ["sudo", "ip", "link", "set", "dev", self.bridge_name, "down"]
        )
        self.iptables(
            [
                "-t",
                "mangle",
                "-D",
                "FORWARD",
                "-i",
                self.bridge_name,
                "-p",
                "tcp",
                "--tcp-flags",
                "SYN,RST",
                "SYN",
                "-j",
                "TCPMSS",
                "--clamp-mss-to-pmtu",
            ]
        )
        self.iptables(
            [
                "-t",
                "nat",
                "-D",
                "POSTROUTING",
                "-s",
                str(self.ipv4_network),
                "!",
                "-d",
                str(self.ipv4_network),
                "-j",
                "MASQUERADE",
            ],
            check=False,
        )
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

    @cached_property
    def _nvidia_container_paths(self):
        """The paths that need to be bind-mounted for NVIDIA CUDA support.

        LXD's security.privileged=true and nvidia.runtime=true options are
        unfortunately incompatible, but we can emulate the important bits of
        the latter with some tactical bind-mounts.  There is no very good
        way to do this; this seems like the least unpleasant approach.
        """
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = "/snap/lxd/current/lib"
        return subprocess.check_output(
            ["/snap/lxd/current/bin/nvidia-container-cli.real", "list"],
            env=env,
            universal_newlines=True,
        ).splitlines()

    def create_profile(self):
        for addr in self.ipv4_network:
            if addr not in (
                self.ipv4_network.network,
                self.ipv4_network.ip,
                self.ipv4_network.broadcast,
            ):
                ipv4_address = netaddr.IPNetwork(
                    (int(addr), self.ipv4_network.prefixlen)
                )
                break
        else:
            raise BackendException(
                "%s has no usable IP addresses" % self.ipv4_network
            )

        try:
            old_profile = self.client.profiles.get(self.profile_name)
        except LXDAPIException:
            pass
        else:
            old_profile.delete()

        raw_lxc_config = [
            ("lxc.cap.drop", ""),
            ("lxc.cap.drop", "sys_time sys_module"),
            ("lxc.cgroup.devices.deny", ""),
            ("lxc.cgroup.devices.allow", ""),
            ("lxc.mount.auto", ""),
            ("lxc.mount.auto", "proc:rw sys:rw"),
            ("lxc.mount.entry","udev /dev devtmpfs rw,nosuid,relatime,size=16181316k,nr_inodes=4045329,mode=755,inode64"),
            ("lxc.autodev", "0"),
        ]

        lxc_version = self._client.host_info["environment"]["driver_version"]
        major, minor = (int(v) for v in lxc_version.split(".")[0:2])

        if major >= 3:
            raw_lxc_config.extend(
                [
                    ("lxc.apparmor.profile", "unconfined"),
                    ("lxc.net.0.ipv4.address", ipv4_address),
                    ("lxc.net.0.ipv4.gateway", self.ipv4_network.ip),
                ]
            )
        else:
            raw_lxc_config.extend(
                [
                    ("lxc.aa_profile", "unconfined"),
                    ("lxc.network.0.ipv4", ipv4_address),
                    ("lxc.network.0.ipv4.gateway", self.ipv4_network.ip),
                ]
            )

        # Linux 4.4 on powerpc doesn't support all the seccomp bits that LXD
        # needs.
        if self.arch == "powerpc":
            raw_lxc_config.append(("lxc.seccomp", ""))
        config = {
            "security.privileged": "true",
            "security.nesting": "true",
            "raw.lxc": "".join(
                f"{key}={value}\n" for key, value in sorted(raw_lxc_config)
            ),
        }
        devices = {
            "eth0": {
                "name": "eth0",
                "nictype": "bridged",
                "parent": self.bridge_name,
                "type": "nic",
            },
        }
        if major >= 3:
            devices["root"] = {
                "path": "/",
                "pool": "default",
                "type": "disk",
            }
        if "gpu-nvidia" in self.constraints:
            for i, path in enumerate(self._nvidia_container_paths):
                # Skip devices here, because bind-mounted devices aren't
                # propagated into snaps (such as lxd) installed inside the
                # container, which causes LXC's nvidia hook to fail.  We'll
                # create the relevant device nodes after the container has
                # started.
                if not path.startswith("/dev/"):
                    devices[f"nvidia-{i}"] = {
                        "path": path,
                        "source": path,
                        "type": "disk",
                    }
        self.client.profiles.create(self.profile_name, config, devices)

    def start(self):
        """See `Backend`."""
        self.stop()

        self.create_profile()
        self.start_bridge()

        container = self.client.containers.create(
            {
                "name": self.name,
                "profiles": [self.profile_name],
                "source": {"type": "image", "alias": self.alias},
            },
            wait=True,
        )

        hostname = subprocess.check_output(
            ["hostname"], universal_newlines=True
        ).rstrip("\n")
        fqdn = subprocess.check_output(
            ["hostname", "--fqdn"], universal_newlines=True
        ).rstrip("\n")
        with self.open("/etc/hosts", mode="a") as hosts_file:
            hosts_file.seek(0, os.SEEK_END)
            if not hosts_file.tell():
                # /etc/hosts is missing or empty
                hosts_file.write(fallback_hosts)
            print(f"\n127.0.1.1\t{fqdn} {hostname}", file=hosts_file)
            os.fchmod(hosts_file.fileno(), 0o644)
        with self.open("/etc/hostname", mode="w+") as hostname_file:
            print(hostname, file=hostname_file)
            os.fchmod(hostname_file.fileno(), 0o644)

        resolv_conf = "/etc/resolv.conf"

        if os.path.islink(resolv_conf):
            resolv_conf = os.path.realpath(resolv_conf)
            if (
                resolv_conf == "/run/systemd/resolve/stub-resolv.conf"
                and os.path.isfile("/run/systemd/resolve/resolv.conf")
            ):
                resolv_conf = "/run/systemd/resolve/resolv.conf"

        self.copy_in(resolv_conf, "/etc/resolv.conf")

        with self.open(
            "/usr/local/sbin/policy-rc.d", mode="w+"
        ) as policy_rc_d_file:
            policy_rc_d_file.write(policy_rc_d)
            os.fchmod(policy_rc_d_file.fileno(), 0o755)
        # For targets that use Upstart, prevent the mounted-dev job from
        # creating devices.  Most of the devices it creates are unnecessary
        # in a container, and creating loop devices will race with our own
        # code to do so.
        if self.path_exists("/etc/init/mounted-dev.conf"):
            with self.open("/etc/init/mounted-dev.conf") as mounted_dev_file:
                script = ""
                in_script = False
                for line in mounted_dev_file:
                    if in_script:
                        script += re.sub(
                            r"^(\s*)(.*MAKEDEV)", r"\1: # \2", line
                        )
                        if line.strip() == "end script":
                            in_script = False
                    elif line.strip() == "script":
                        script += line
                        in_script = True

            if script:
                with self.open(
                    "/etc/init/mounted-dev.override", mode="w"
                ) as mounted_dev_override_file:
                    mounted_dev_override_file.write(script)
                    os.fchmod(mounted_dev_override_file.fileno(), 0o644)

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
                "Container failed to start within %d seconds" % timeout
            )

        # Create dm-# devices.  On focal kpartx looks for dm devices and hangs
        # in their absence.
        major = get_device_mapper_major()
        for minor in range(8):
            self.run(
                [
                    "mknod",
                    "-m",
                    "0660",
                    "/dev/dm-%d" % minor,
                    "b",
                    str(major),
                    str(minor),
                ]
            )

        if "gpu-nvidia" in self.constraints:
            # We bind-mounted several libraries into the container, so run
            # ldconfig to update the dynamic linker's cache.
            self.run(["/sbin/ldconfig"])

        # XXX cjwatson 2017-09-07: With LXD < 2.2 we can't create the
        # directory until the container has started.  We can get away with
        # this for the time being because snapd isn't in the buildd chroots.
        self.run(["mkdir", "-p", "/etc/systemd/system/snapd.service.d"])
        with self.open(
            "/etc/systemd/system/snapd.service.d/no-cdn.conf", mode="w+"
        ) as no_cdn_file:
            print(
                dedent(
                    """\
                [Service]
                Environment=SNAPPY_STORE_NO_CDN=1
                """
                ),
                file=no_cdn_file,
                end="",
            )
            os.fchmod(no_cdn_file.fileno(), 0o644)

        # Refreshing snaps from a timer unit during a build isn't
        # appropriate.  Mask this, but manually so that we don't depend on
        # systemctl existing.  This relies on /etc/systemd/system/ having
        # been created above.
        self.run(
            [
                "ln",
                "-s",
                "/dev/null",
                "/etc/systemd/system/snapd.refresh.timer",
            ]
        )

        if self.arch == "armhf":
            # Work around https://github.com/lxc/lxcfs/issues/553.  In
            # principle that could result in over-reporting the number of
            # available CPU cores, but that isn't a concern in
            # launchpad-buildd.
            try:
                self.run(["umount", "/proc/cpuinfo"])
            except subprocess.CalledProcessError:
                pass

    def run(
        self,
        args,
        cwd=None,
        env=None,
        input_text=None,
        get_output=False,
        echo=False,
        return_process=False,
        **kwargs,
    ):
        """See `Backend`."""
        env_params = []
        if env:
            for key, value in env.items():
                env_params.extend(["--env", f"{key}={value}"])
        if self.arch is not None:
            args = set_personality(args, self.arch, series=self.series)
        if cwd is not None:
            # This requires either a helper program in the chroot or
            # unpleasant quoting.  For now we go for the unpleasant quoting,
            # though once we have coreutils >= 8.28 everywhere we'll be able
            # to use "env --chdir".
            escaped_args = " ".join(shell_escape(arg) for arg in args)
            args = [
                "/bin/sh",
                "-c",
                f"cd {shell_escape(cwd)} && {escaped_args}",
            ]
        if echo:
            print(
                "Running in container: %s"
                % " ".join(shell_escape(arg) for arg in args)
            )
        # pylxd's Container.execute doesn't support sending stdin, and it's
        # tedious to implement ourselves.
        cmd = ["lxc", "exec", self.name] + env_params + ["--"] + args
        if input_text is None and not get_output:
            subprocess.check_call(cmd, **kwargs)
        else:
            if get_output:
                kwargs["stdout"] = subprocess.PIPE
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, **kwargs)
            if return_process:
                return proc
            output, _ = proc.communicate(input_text)
            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, cmd)
            if get_output:
                if echo:
                    print("Output:")
                    output_text = output
                    if isinstance(output_text, bytes):
                        output_text = output_text.decode("UTF-8", "replace")
                    print(output_text)
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
                "X-LXD-uid": "0",
                "X-LXD-gid": "0",
                # Go (and hence LXD) only supports 0o prefixes for octal
                # numbers as of Go 1.13, and it's not clear that we can
                # assume this.  Use plain 0 prefixes instead.
                "X-LXD-mode": "0%o" % mode if mode else "0",
            }
            try:
                container.api.files.post(
                    params=params, data=data, headers=headers
                )
            except LXDAPIException as e:
                raise LXDException(
                    f"Failed to push {self.name}:{target_path}", e
                )

    def _get_file(self, container, *args, **kwargs):
        # pylxd < 2.1.1 tries to validate the response as JSON in streaming
        # mode and ends up running out of memory on large files.  Work
        # around this.
        response = container.api.files.session.get(
            container.api.files._api_endpoint, *args, **kwargs
        )
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
            try:
                with closing(
                    self._get_file(container, params=params, stream=True)
                ) as response:
                    for chunk in response.iter_content(chunk_size=65536):
                        target_file.write(chunk)
            except LXDAPIException as e:
                raise LXDException(
                    f"Failed to pull {self.name}:{source_path}", e
                )

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
        super().remove()
