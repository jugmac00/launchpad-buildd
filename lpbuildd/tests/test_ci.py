# Copyright 2022 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import os
import shutil

from fixtures import EnvironmentVariable, TempDir
from testtools import TestCase
from testtools.twistedsupport import AsynchronousDeferredRunTest
from twisted.internet import defer

from lpbuildd.builder import get_build_path
from lpbuildd.ci import (
    RESULT_FAILED,
    RESULT_SUCCEEDED,
    RETCODE_FAILURE_BUILD,
    RETCODE_SUCCESS,
    CIBuildManager,
    CIBuildState,
)
from lpbuildd.tests.fakebuilder import FakeBuilder
from lpbuildd.tests.matchers import HasWaitingFiles


class MockBuildManager(CIBuildManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commands = []
        self.iterators = []

    def runSubProcess(self, path, command, iterate=None, env=None):
        self.commands.append([path] + command)
        if iterate is None:
            iterate = self.iterate
        self.iterators.append(iterate)
        return 0


class TestCIBuildManagerIteration(TestCase):
    """Run CIBuildManager through its iteration steps."""

    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=5)

    def setUp(self):
        super().setUp()
        self.working_dir = self.useFixture(TempDir()).path
        builder_dir = os.path.join(self.working_dir, "builder")
        home_dir = os.path.join(self.working_dir, "home")
        for dir in (builder_dir, home_dir):
            os.mkdir(dir)
        self.useFixture(EnvironmentVariable("HOME", home_dir))
        self.builder = FakeBuilder(builder_dir)
        self.buildid = "123"
        self.buildmanager = MockBuildManager(self.builder, self.buildid)
        self.buildmanager._cachepath = self.builder._cachepath

    def getState(self):
        """Retrieve build manager's state."""
        return self.buildmanager._state

    @defer.inlineCallbacks
    def startBuild(self, args=None, options=None, constraints=None):
        # The build manager's iterate() kicks off the consecutive states
        # after INIT.
        extra_args = {
            "series": "focal",
            "arch_tag": "amd64",
            "name": "test",
        }
        if args is not None:
            extra_args.update(args)
        original_backend_name = self.buildmanager.backend_name
        self.buildmanager.backend_name = "fake"
        self.buildmanager.initiate({}, "chroot.tar.gz", extra_args)
        self.buildmanager.backend_name = original_backend_name

        # Skip states that are done in DebianBuildManager to the state
        # directly before PREPARE.
        self.buildmanager._state = CIBuildState.UPDATE

        # PREPARE: Run the builder's payload to prepare for running CI jobs.
        yield self.buildmanager.iterate(0)
        self.assertEqual(CIBuildState.PREPARE, self.getState())
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "run-ci-prepare",
            "--backend=lxd",
            "--series=focal",
            "--arch=amd64",
        ]
        for constraint in constraints or []:
            expected_command.append("--constraint=%s" % constraint)
        expected_command.append(self.buildid)
        if options is not None:
            expected_command.extend(options)
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("chrootFail"))

    @defer.inlineCallbacks
    def expectRunJob(
        self, job_name, job_index, options=None, retcode=RETCODE_SUCCESS
    ):
        yield self.buildmanager.iterate(retcode)
        self.assertEqual(CIBuildState.RUN_JOB, self.getState())
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "run-ci",
            "--backend=lxd",
            "--series=focal",
            "--arch=amd64",
            self.buildid,
        ]
        if options is not None:
            expected_command.extend(options)
        expected_command.extend([job_name, job_index])
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("chrootFail"))

    @defer.inlineCallbacks
    def test_iterate_success(self):
        # The build manager iterates multiple CI jobs from start to finish.
        args = {
            "git_repository": "https://git.launchpad.test/~example/+git/ci",
            "git_path": "main",
            "jobs": [[("build", "0")], [("test", "0")]],
            "package_repositories": ["repository one", "repository two"],
            "environment_variables": {
                "INDEX": "http://example.com",
                "PATH": "foo",
            },
            "plugin_settings": {
                "miniconda_conda_channel": "https://user:pass@canonical.example.com/artifactory/soss-conda-stable-local/",  # noqa: E501
                "foo": "bar",
            },
            "secrets": {
                "auth": "user:pass",
            },
            "scan_malware": True,
        }
        expected_prepare_options = [
            "--git-repository",
            "https://git.launchpad.test/~example/+git/ci",
            "--git-path",
            "main",
            "--scan-malware",
        ]
        yield self.startBuild(args, expected_prepare_options)

        # After preparation, start running the first job.
        expected_job_options = [
            "--package-repository",
            "repository one",
            "--package-repository",
            "repository two",
            "--environment-variable",
            "INDEX=http://example.com",
            "--environment-variable",
            "PATH=foo",
            "--plugin-setting",
            "miniconda_conda_channel=https://user:pass@canonical.example.com/artifactory/soss-conda-stable-local/",  # noqa: E501
            "--plugin-setting",
            "foo=bar",
            "--secrets",
            "/build/.launchpad-secrets.yaml",
            "--scan-malware",
        ]
        yield self.expectRunJob("build", "0", options=expected_job_options)
        self.buildmanager.backend.add_file(
            "/build/output/build/0/log", b"I am a CI build job log."
        )
        self.buildmanager.backend.add_file(
            "/build/output/build/0/files/ci.whl",
            b"I am output from a CI build job.",
        )
        self.buildmanager.backend.add_file(
            "/build/output/build/0/properties", b'{"key": "value"}'
        )

        # Collect the output of the first job and start running the second.
        yield self.expectRunJob("test", "0", options=expected_job_options)
        self.buildmanager.backend.add_file(
            "/build/output/test/0/log", b"I am a CI test job log."
        )
        self.buildmanager.backend.add_file(
            "/build/output/test/0/files/ci.tar.gz",
            b"I am output from a CI test job.",
        )

        # Output from the first job is visible in the status response.
        extra_status = self.buildmanager.status()
        self.assertEqual(
            {
                "build:0": {
                    "log": self.builder.waitingfiles["build:0.log"],
                    "properties": (
                        self.builder.waitingfiles["build:0.properties"]
                    ),
                    "output": {
                        "ci.whl": self.builder.waitingfiles["build:0/ci.whl"],
                    },
                    "result": RESULT_SUCCEEDED,
                },
            },
            extra_status["jobs"],
        )

        # After running the final job, reap processes.
        yield self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "scan-for-processes",
            "--backend=lxd",
            "--series=focal",
            "--arch=amd64",
            self.buildid,
        ]
        self.assertEqual(CIBuildState.RUN_JOB, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "build:0.log": b"I am a CI build job log.",
                    "build:0.properties": b'{"key": "value"}',
                    "build:0/ci.whl": b"I am output from a CI build job.",
                    "test:0.log": b"I am a CI test job log.",
                    "test:0/ci.tar.gz": b"I am output from a CI test job.",
                }
            ),
        )

        # Output from both jobs is visible in the status response.
        extra_status = self.buildmanager.status()
        self.assertEqual(
            {
                "build:0": {
                    "log": self.builder.waitingfiles["build:0.log"],
                    "properties": (
                        self.builder.waitingfiles["build:0.properties"]
                    ),
                    "output": {
                        "ci.whl": self.builder.waitingfiles["build:0/ci.whl"],
                    },
                    "result": RESULT_SUCCEEDED,
                },
                "test:0": {
                    "log": self.builder.waitingfiles["test:0.log"],
                    "output": {
                        "ci.tar.gz": self.builder.waitingfiles[
                            "test:0/ci.tar.gz"
                        ],
                    },
                    "result": RESULT_SUCCEEDED,
                },
            },
            extra_status["jobs"],
        )

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "umount-chroot",
            "--backend=lxd",
            "--series=focal",
            "--arch=amd64",
            self.buildid,
        ]
        self.assertEqual(CIBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertFalse(self.builder.wasCalled("buildFail"))

        # If we iterate to the end of the build, then the extra status
        # information is still present.
        self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "remove-build",
            "--backend=lxd",
            "--series=focal",
            "--arch=amd64",
            self.buildid,
        ]
        self.assertEqual(CIBuildState.CLEANUP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )

        self.buildmanager.iterate(0)
        self.assertTrue(self.builder.wasCalled("buildOK"))
        self.assertTrue(self.builder.wasCalled("buildComplete"))
        # remove-build would remove this in a non-test environment.
        shutil.rmtree(
            get_build_path(self.buildmanager.home, self.buildmanager._buildid)
        )
        self.assertIn("jobs", self.buildmanager.status())

    @defer.inlineCallbacks
    def test_iterate_failure(self):
        # The build manager records CI jobs that fail.
        args = {
            "git_repository": "https://git.launchpad.test/~example/+git/ci",
            "git_path": "main",
            "jobs": [[("lint", "0"), ("build", "0")], [("test", "0")]],
            "package_repositories": ["repository one", "repository two"],
            "environment_variables": {
                "INDEX": "http://example.com",
                "PATH": "foo",
            },
            "plugin_settings": {
                "miniconda_conda_channel": "https://user:pass@canonical.example.com/artifactory/soss-conda-stable-local/",  # noqa: E501
                "foo": "bar",
            },
            "secrets": {
                "auth": "user:pass",
            },
        }
        expected_prepare_options = [
            "--git-repository",
            "https://git.launchpad.test/~example/+git/ci",
            "--git-path",
            "main",
        ]
        yield self.startBuild(args, expected_prepare_options)

        # After preparation, start running the first job.
        expected_job_options = [
            "--package-repository",
            "repository one",
            "--package-repository",
            "repository two",
            "--environment-variable",
            "INDEX=http://example.com",
            "--environment-variable",
            "PATH=foo",
            "--plugin-setting",
            "miniconda_conda_channel=https://user:pass@canonical.example.com/artifactory/soss-conda-stable-local/",  # noqa: E501
            "--plugin-setting",
            "foo=bar",
            "--secrets",
            "/build/.launchpad-secrets.yaml",
        ]
        yield self.expectRunJob("lint", "0", options=expected_job_options)
        self.buildmanager.backend.add_file(
            "/build/output/lint/0/log", b"I am a failing CI lint job log."
        )

        # Collect the output of the first job and start running the second.
        # (Note that `retcode` is the return code of the *first* job, not the
        # second.)
        yield self.expectRunJob(
            "build",
            "0",
            options=expected_job_options,
            retcode=RETCODE_FAILURE_BUILD,
        )
        self.buildmanager.backend.add_file(
            "/build/output/build/0/log", b"I am a CI build job log."
        )

        # Output from the first job is visible in the status response.
        extra_status = self.buildmanager.status()
        self.assertEqual(
            {
                "lint:0": {
                    "log": self.builder.waitingfiles["lint:0.log"],
                    "result": RESULT_FAILED,
                },
            },
            extra_status["jobs"],
        )

        # Since the first pipeline stage failed, we won't go any further, and
        # expect to start reaping processes.
        yield self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "scan-for-processes",
            "--backend=lxd",
            "--series=focal",
            "--arch=amd64",
            self.buildid,
        ]
        self.assertEqual(CIBuildState.RUN_JOB, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertNotEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertTrue(self.builder.wasCalled("buildFail"))
        self.assertThat(
            self.builder,
            HasWaitingFiles.byEquality(
                {
                    "lint:0.log": b"I am a failing CI lint job log.",
                    "build:0.log": b"I am a CI build job log.",
                }
            ),
        )

        # Output from the two jobs in the first pipeline stage is visible in
        # the status response.
        extra_status = self.buildmanager.status()
        self.assertEqual(
            {
                "lint:0": {
                    "log": self.builder.waitingfiles["lint:0.log"],
                    "result": RESULT_FAILED,
                },
                "build:0": {
                    "log": self.builder.waitingfiles["build:0.log"],
                    "result": RESULT_SUCCEEDED,
                },
            },
            extra_status["jobs"],
        )

        # Control returns to the DebianBuildManager in the UMOUNT state.
        self.buildmanager.iterateReap(self.getState(), 0)
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "umount-chroot",
            "--backend=lxd",
            "--series=focal",
            "--arch=amd64",
            self.buildid,
        ]
        self.assertEqual(CIBuildState.UMOUNT, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )
        self.assertTrue(self.builder.wasCalled("buildFail"))

        # If we iterate to the end of the build, then the extra status
        # information is still present.
        self.buildmanager.iterate(0)
        expected_command = [
            "sharepath/bin/in-target",
            "in-target",
            "remove-build",
            "--backend=lxd",
            "--series=focal",
            "--arch=amd64",
            self.buildid,
        ]
        self.assertEqual(CIBuildState.CLEANUP, self.getState())
        self.assertEqual(expected_command, self.buildmanager.commands[-1])
        self.assertEqual(
            self.buildmanager.iterate, self.buildmanager.iterators[-1]
        )

        self.buildmanager.iterate(0)
        self.assertFalse(self.builder.wasCalled("buildOK"))
        self.assertTrue(self.builder.wasCalled("buildComplete"))
        # remove-build would remove this in a non-test environment.
        shutil.rmtree(
            get_build_path(self.buildmanager.home, self.buildmanager._buildid)
        )
        self.assertIn("jobs", self.buildmanager.status())

    @defer.inlineCallbacks
    def test_iterate_with_clamav_database_url(self):
        # If proxy.clamavdatabase is set, the build manager passes it via
        # the --clamav-database-url option.
        self.builder._config.set(
            "proxy", "clamavdatabase", "http://clamav.example/"
        )
        args = {
            "git_repository": "https://git.launchpad.test/~example/+git/ci",
            "git_path": "main",
            "jobs": [[("build", "0")], [("test", "0")]],
            "scan_malware": True,
        }
        expected_prepare_options = [
            "--git-repository",
            "https://git.launchpad.test/~example/+git/ci",
            "--git-path",
            "main",
            "--scan-malware",
            "--clamav-database-url",
            "http://clamav.example/",
        ]
        yield self.startBuild(args, expected_prepare_options)

    @defer.inlineCallbacks
    def test_constraints(self):
        # The build manager passes constraints to subprocesses.
        args = {
            "builder_constraints": ["one", "two"],
            "git_repository": "https://git.launchpad.test/~example/+git/ci",
            "git_path": "main",
            "jobs": [[("build", "0")], [("test", "0")]],
        }
        expected_prepare_options = [
            "--git-repository",
            "https://git.launchpad.test/~example/+git/ci",
            "--git-path",
            "main",
        ]
        yield self.startBuild(
            args, expected_prepare_options, constraints=["one", "two"]
        )
