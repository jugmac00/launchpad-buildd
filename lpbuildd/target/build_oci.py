# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

from collections import OrderedDict
import json
import logging
import os.path
import re
import sys
import tempfile
from textwrap import dedent

from lpbuildd.target.operation import Operation
from lpbuildd.target.snapbuildproxy import SnapBuildProxyOperationMixin
from lpbuildd.target.snapstore import SnapStoreOperationMixin
from lpbuildd.target.vcs import VCSOperationMixin


RETCODE_FAILURE_INSTALL = 200
RETCODE_FAILURE_BUILD = 201


logger = logging.getLogger(__name__)


class InvalidBuildFilePath(Exception):
    pass


class BuildOCI(SnapBuildProxyOperationMixin, VCSOperationMixin,
               SnapStoreOperationMixin, Operation):

    description = "Build an OCI image."

    @classmethod
    def add_arguments(cls, parser):
        super(BuildOCI, cls).add_arguments(parser)
        parser.add_argument(
            "--build-file", help="path to Dockerfile in branch")
        parser.add_argument(
            "--build-path", default=".",
            help="context directory for docker build")
        parser.add_argument(
            "--build-arg", default=[], action='append',
            help="A docker build ARG in the format of key=value. "
                 "This option can be repeated many times. For example: "
                 "--build-arg VAR1=A --build-arg VAR2=B")
        parser.add_argument(
            "--metadata", default=None,
            help="Metadata about this build, used to generate manifest file.")
        parser.add_argument("name", help="name of snap to build")

    def __init__(self, args, parser):
        super(BuildOCI, self).__init__(args, parser)
        self.bin = os.path.dirname(sys.argv[0])
        self.buildd_path = os.path.join("/home/buildd", self.args.name)
        # Temp directory where we store files that will be included in the
        # final filesystem of the image.
        self.backend_tmp_fs_dir = "/tmp/image-root-dir/"
        self.security_manifest_target_path = "/.rocks/manifest.json"

    def _add_docker_engine_proxy_settings(self):
        """Add systemd file for docker proxy settings."""
        # Create containing directory for systemd overrides
        self.backend.run(
            ["mkdir", "-p", "/etc/systemd/system/docker.service.d"])
        # we need both http_proxy and https_proxy. The contents of the files
        # are otherwise identical
        for setting in ['http_proxy', 'https_proxy']:
            contents = dedent("""[Service]
                Environment="{}={}"
                """.format(setting.upper(), self.args.proxy_url))
            file_path = "/etc/systemd/system/docker.service.d/{}.conf".format(
                setting)
            with tempfile.NamedTemporaryFile(mode="w+") as systemd_file:
                systemd_file.write(contents)
                systemd_file.flush()
                self.backend.copy_in(systemd_file.name, file_path)

    def _check_path_escape(self, path_to_check):
        """Check the build file path doesn't escape the build directory."""
        build_file_path = os.path.realpath(
            os.path.join(self.buildd_path, path_to_check))
        common_path = os.path.commonprefix((build_file_path, self.buildd_path))
        if common_path != self.buildd_path:
            raise InvalidBuildFilePath("Invalid build file path.")

    def run_build_command(self, args, env=None, **kwargs):
        """Run a build command in the target.

        :param args: the command and arguments to run.
        :param env: dictionary of additional environment variables to set.
        :param kwargs: any other keyword arguments to pass to Backend.run.
        """
        full_env = OrderedDict()
        full_env["LANG"] = "C.UTF-8"
        full_env["SHELL"] = "/bin/sh"
        if env:
            full_env.update(env)
        return self.backend.run(
            args, cwd=self.buildd_path, env=full_env, **kwargs)

    def install(self):
        logger.info("Running install phase...")
        deps = []
        if self.args.proxy_url:
            deps.extend(self.proxy_deps)
            self.install_git_proxy()
            # Add any proxy settings that are needed
            self._add_docker_engine_proxy_settings()
        deps.extend(self.vcs_deps)
        # Install dctrl-tools to extract installed packages using grep-dctrl.
        deps.extend(["docker.io", "dctrl-tools"])
        self.backend.run(["apt-get", "-y", "install"] + deps)
        if self.args.backend in ("lxd", "fake"):
            self.snap_store_set_proxy()
        self.backend.run(["systemctl", "restart", "docker"])
        # The docker snap can't see /build, so we have to do our work under
        # /home/buildd instead.  Make sure it exists.
        self.backend.run(["mkdir", "-p", "/home/buildd"])

    def repo(self):
        """Collect git or bzr branch."""
        logger.info("Running repo phase...")
        env = self.build_proxy_environment(proxy_url=self.args.proxy_url)
        self.vcs_fetch(self.args.name, cwd="/home/buildd", env=env)

    def _getCurrentVCSRevision(self):
        if self.args.branch is not None:
            revision_cmd = ["bzr", "revno"]
        else:
            revision_cmd = ["git", "rev-parse", "HEAD"]
        return self.backend.run(
            revision_cmd, cwd=os.path.join("/home/buildd", self.args.name),
            get_output=True).decode("UTF-8", "replace").strip()

    def _getContainerPackageList(self):
        tmp_file = "/tmp/dpkg-status"
        self.run_build_command([
            "docker", "cp", "-L",
            "%s:/var/lib/dpkg/status" % self.args.name, tmp_file])
        output = self.backend.run([
            "grep-dctrl", "-s", "Package,Version,Source", "", tmp_file],
            get_output=True).decode("UTF-8", "replace")
        packages = []
        empty_pkg_details = dict.fromkeys(["package", "version", "source"])
        current_package = empty_pkg_details.copy()
        for line in output.split("\n"):
            if not line.strip():
                if not all(i is None for i in current_package.values()):
                    packages.append(current_package)
                    current_package = empty_pkg_details.copy()
                continue
            k, v = line.split(":", 1)
            current_package[k.lower().strip()] = v.strip()
        if not all(i is None for i in current_package.values()):
            packages.append(current_package)
        return packages

    def _getContainerOSRelease(self):
        tmp_file = "/tmp/os-release"
        self.run_build_command([
            "docker", "cp",  "-L",
            "%s:/etc/os-release" % self.args.name, tmp_file])
        content = self.backend.run(["cat", tmp_file], get_output=True)
        os_release = {}
        # Variable content might be enclosed by double-quote, single-quote
        # or no quote at all. We accept everything.
        content_expr = re.compile(r""""(.*)"|'(.*)'|(.*)""")
        unquote = lambda string: [
            i for i in content_expr.match(string).groups() if i is not None][0]
        for line in content.decode("UTF-8", "replace").split("\n"):
            if '=' not in line:
                continue
            key, value = line.strip().split("=", 1)
            os_release[key] = unquote(value)
        return os_release

    def _getSecurityManifestContent(self):
        try:
            metadata = json.loads(self.args.metadata) or {}
        except TypeError:
            metadata = {}
        recipe_owner = metadata.get("recipe_owner", {})
        build_requester = metadata.get("build_requester", {})
        emails = [i.get("email") for i in (recipe_owner, build_requester)
                  if i.get("email")]

        try:
            packages = self._getContainerPackageList()
        except Exception as e:
            logger.warning("Failed to get container package list: %s", e)
            packages = []
        try:
            vcs_current_version = self._getCurrentVCSRevision()
        except Exception as e:
            logger.warning("Failed to get current VCS revision: %s" % e)
            vcs_current_version = None
        try:
            os_release = self._getContainerOSRelease()
        except Exception as e:
            logger.warning("Failed to get /etc/os-release info: %s" % e)
            os_release = {}

        return {
            "manifest-version": "1",
            "name": self.args.name,
            "os-release-id": os_release.get("ID"),
            "os-release-version-id": os_release.get("VERSION_ID"),
            "architectures": metadata.get("architectures") or [self.args.arch],
            "publisher-emails": emails,
            "image-info": {
                "build-request-id": metadata.get("build_request_id"),
                "build-request-timestamp": metadata.get(
                    "build_request_timestamp"),
                "build-urls": metadata.get("build_urls") or {}
            },
            "vcs-info": [{
                "source": self.args.git_repository,
                "source-branch": self.args.git_path,
                "source-commit": vcs_current_version,
                "source-subdir": self.args.build_path,
                "source-build-file": self.args.build_file,
                "source-build-args": self.args.build_arg
            }],
            "packages": packages
        }

    def createSecurityManifest(self):
        """Generates the security manifest file, returning the tmp file name
        where it is stored in the backend.
        """
        content = self._getSecurityManifestContent()
        local_filename = tempfile.mktemp()
        destination_path = self.security_manifest_target_path.lstrip(
            os.path.sep)
        destination = os.path.join(self.backend_tmp_fs_dir, destination_path)
        logger.info("Security manifest: %s" % content)
        with open(local_filename, 'w') as fd:
            json.dump(content, fd, indent=2)
        self.backend.copy_in(local_filename, destination)
        return destination

    def initTempRootDir(self):
        """Initialize in the backend the directories that will be included in
        resulting image's filesystem.
        """
        security_manifest_dir = os.path.dirname(
            self.security_manifest_target_path)
        dir = os.path.join(
            self.backend_tmp_fs_dir,
            security_manifest_dir.lstrip(os.path.sep))
        self.backend.run(["mkdir", "-p", dir])

    def createImageContainer(self):
        """Creates a container from the built image, so we can play with
        it's filesystem."""
        self.run_build_command([
            "docker", "create", "--name", self.args.name, self.args.name])

    def removeImageContainer(self):
        self.run_build_command(["docker", "rm", self.args.name])

    def commitImage(self):
        """Commits the tmp container, overriding the originally built image."""
        self.run_build_command([
            "docker", "commit", self.args.name, self.args.name])

    def addFilesToImageContainer(self):
        """Flushes all files from temp root dir (in the backend) to the
        resulting image container."""
        # The extra '.' in the end is important. It indicates to docker that
        # the directory itself should be copied, instead of the list of
        # files in the directory. It makes docker keep the paths.
        src = os.path.join(self.backend_tmp_fs_dir, ".")
        self.run_build_command(["docker", "cp", src, "%s:/" % self.args.name])

    def addSecurityManifest(self):
        self.createImageContainer()
        self.initTempRootDir()
        self.createSecurityManifest()
        self.addFilesToImageContainer()
        self.commitImage()
        self.removeImageContainer()

    def build(self):
        logger.info("Running build phase...")
        args = ["docker", "build", "--no-cache"]
        if self.args.proxy_url:
            for var in ("http_proxy", "https_proxy"):
                args.extend(
                    ["--build-arg", "{}={}".format(var, self.args.proxy_url)])
        args.extend(["--tag", self.args.name])
        if self.args.build_file is not None:
            build_file_path = os.path.join(
                self.args.build_path, self.args.build_file)
            self._check_path_escape(build_file_path)
            args.extend(["--file", build_file_path])

        # Keep this at the end, so we give the user a chance to override any
        # build-arg we set automatically (like http_proxy).
        for arg in self.args.build_arg:
            args.extend(["--build-arg=%s" % arg])

        build_context_path = os.path.join(
            self.buildd_path, self.args.build_path)
        self._check_path_escape(build_context_path)
        args.append(build_context_path)
        self.run_build_command(args)
        self.addSecurityManifest()

    def run(self):
        try:
            self.install()
        except Exception:
            logger.exception('Install failed')
            return RETCODE_FAILURE_INSTALL
        try:
            self.repo()
            self.build()
        except Exception:
            logger.exception('Build failed')
            return RETCODE_FAILURE_BUILD
        return 0
