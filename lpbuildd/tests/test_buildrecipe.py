# Copyright 2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

__metaclass__ = type

from contextlib import contextmanager
import imp
import os
import shutil
import sys
import tempfile
from textwrap import dedent

from testtools import TestCase


@contextmanager
def disable_bytecode():
    original = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    yield
    sys.dont_write_bytecode = original


# By-hand import to avoid having to put .py suffixes on slave binaries.
with disable_bytecode():
    RecipeBuilder = imp.load_source(
        "buildrecipe", "bin/buildrecipe").RecipeBuilder


class TestRecipeBuilder(TestCase):
    def setUp(self):
        super(TestRecipeBuilder, self).setUp()
        self.save_env = dict(os.environ)
        self.home_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.home_dir))
        os.environ["HOME"] = self.home_dir
        self.build_id = "1"
        self.builder = RecipeBuilder(
            self.build_id, "Recipe Builder", "builder@example.org>", "grumpy",
            "grumpy", "main", "PPA")
        os.makedirs(self.builder.work_dir)

    def resetEnvironment(self):
        for key in set(os.environ.keys()) - set(self.save_env.keys()):
            del os.environ[key]
        for key, value in os.environ.items():
            if value != self.save_env[key]:
                os.environ[key] = self.save_env[key]
        for key in set(self.save_env.keys()) - set(os.environ.keys()):
            os.environ[key] = self.save_env[key]

    def tearDown(self):
        self.resetEnvironment()
        super(TestRecipeBuilder, self).tearDown()

    def test_makeDummyDsc(self):
        self.builder.source_dir_relative = os.path.join(
            self.builder.work_dir_relative, "tree", "foo")
        control_path = os.path.join(
            self.builder.work_dir, "tree", "foo", "debian", "control")
        os.makedirs(os.path.dirname(control_path))
        os.makedirs(self.builder.apt_dir)
        with open(control_path, "w") as control:
            print(dedent("""\
                Source: foo
                Build-Depends: debhelper (>= 9~), libfoo-dev

                Package: foo
                Depends: ${shlibs:Depends}"""),
                file=control)
        self.builder.makeDummyDsc("foo")
        with open(os.path.join(self.builder.apt_dir, "foo.dsc")) as dsc:
            self.assertEqual(
                dedent("""\
                    Format: 1.0
                    Source: foo
                    Architecture: any
                    Version: 99:0
                    Maintainer: invalid@example.org
                    Build-Depends: debhelper (>= 9~), libfoo-dev

                    """),
                dsc.read())

    def test_makeDummyDsc_comments(self):
        # apt_pkg.TagFile doesn't support comments, but python-debian's own
        # parser does.  Make sure we're using the right one.
        self.builder.source_dir_relative = os.path.join(
            self.builder.work_dir_relative, "tree", "foo")
        control_path = os.path.join(
            self.builder.work_dir, "tree", "foo", "debian", "control")
        os.makedirs(os.path.dirname(control_path))
        os.makedirs(self.builder.apt_dir)
        with open(control_path, "w") as control:
            print(dedent("""\
                Source: foo
                Build-Depends: debhelper (>= 9~),
                               libfoo-dev,
                # comment line
                               pkg-config

                Package: foo
                Depends: ${shlibs:Depends}"""),
                file=control)
        self.builder.makeDummyDsc("foo")
        with open(os.path.join(self.builder.apt_dir, "foo.dsc")) as dsc:
            self.assertEqual(
                dedent("""\
                    Format: 1.0
                    Source: foo
                    Architecture: any
                    Version: 99:0
                    Maintainer: invalid@example.org
                    Build-Depends: debhelper (>= 9~),
                                   libfoo-dev,
                                   pkg-config

                    """),
                dsc.read())

    def test_runAptFtparchive(self):
        os.makedirs(self.builder.apt_dir)
        with open(os.path.join(self.builder.apt_dir, "foo.dsc"), "w") as dsc:
            print(dedent("""\
                Format: 1.0
                Source: foo
                Architecture: any
                Version: 99:0
                Maintainer: invalid@example.org
                Build-Depends: debhelper (>= 9~), libfoo-dev"""),
                file=dsc)
        self.assertEqual(0, self.builder.runAptFtparchive())
        self.assertEqual(
            ["Release", "Sources", "Sources.bz2", "foo.dsc",
             "ftparchive.conf"],
            sorted(os.listdir(self.builder.apt_dir)))
        with open(os.path.join(self.builder.apt_dir, "Sources")) as sources:
            sources_text = sources.read()
            self.assertIn("Package: foo\n", sources_text)
            self.assertIn(
                "Build-Depends: debhelper (>= 9~), libfoo-dev\n", sources_text)

    # XXX cjwatson 2015-06-15: We should unit-test enableAptArchive and
    # installBuildDeps too, but it involves a lot of mocks.  For now,
    # integration testing is probably more useful.
