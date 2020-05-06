# Copyright 2011-2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import re

from six import StringIO
from testtools import TestCase
from testtools.matchers import MatchesRegex

from lpbuildd.check_implicit_pointer_functions import (
    filter_log,
    implicit_pattern,
    pointer_pattern,
    )


class TestPointerCheckRegexes(TestCase):

    def test_catches_pointer_from_integer_without_column_number(self):
        # Regex should match compiler errors that don't include the
        # column number.
        line = (
            "/build/buildd/gtk+3.0-3.0.0/./gtk/ubuntumenuproxymodule.c:94: "
            "warning: assignment makes pointer from integer without a cast")
        self.assertIsNot(None, pointer_pattern.match(line))

    def test_catches_pointer_from_integer_with_column_number(self):
        # Regex should match compiler errors that do include the
        # column number.
        line = (
            "/build/buildd/gtk+3.0-3.0.0/./gtk/ubuntumenuproxymodule.c:94:7: "
            "warning: assignment makes pointer from integer without a cast")
        self.assertIsNot(None, pointer_pattern.match(line))

    def test_catches_implicit_function_without_column_number(self):
        # Regex should match compiler errors that don't include the
        # column number.
        line = (
            "/build/buildd/gtk+3.0-3.0.0/./gtk/ubuntumenuproxymodule.c:94: "
            "warning: implicit declaration of function 'foo'")
        self.assertIsNot(None, implicit_pattern.match(line))

    def test_catches_implicit_function_with_column_number(self):
        # Regex should match compiler errors that do include the
        # column number.
        line = (
            "/build/buildd/gtk+3.0-3.0.0/./gtk/ubuntumenuproxymodule.c:94:7: "
            "warning: implicit declaration of function 'foo'")
        self.assertIsNot(None, implicit_pattern.match(line))


class TestFilterLog(TestCase):

    def test_out_of_line_no_errors(self):
        in_file = StringIO("Innocuous build log\nwith no errors\n")
        out_file = StringIO()
        self.assertEqual(0, filter_log(in_file, out_file))
        self.assertEqual("", out_file.getvalue())

    def test_out_of_line_errors(self):
        in_file = StringIO(
            "Build log with errors\n"
            "/build/buildd/gtk+3.0-3.0.0/./gtk/ubuntumenuproxymodule.c:94: "
            "warning: implicit declaration of function 'foo'\n"
            "/build/buildd/gtk+3.0-3.0.0/./gtk/ubuntumenuproxymodule.c:94: "
            "warning: assignment makes pointer from integer without a cast\n"
            "More build log\n")
        out_file = StringIO()
        self.assertEqual(1, filter_log(in_file, out_file))
        self.assertEqual(
            "Function `foo' implicitly converted to pointer at "
            "/build/buildd/gtk+3.0-3.0.0/./gtk/ubuntumenuproxymodule.c:94\n",
            out_file.getvalue())

    def test_in_line_no_errors(self):
        in_file = StringIO("Innocuous build log\nwith no errors\n")
        out_file = StringIO()
        self.assertEqual(0, filter_log(in_file, out_file, in_line=True))
        self.assertEqual(
            "Innocuous build log\nwith no errors\n", out_file.getvalue())

    def test_in_line_errors(self):
        in_file = StringIO(
            "Build log with errors\n"
            "/build/gtk/ubuntumenuproxymodule.c:94: "
            "warning: implicit declaration of function 'foo'\n"
            "/build/gtk/ubuntumenuproxymodule.c:94: "
            "warning: assignment makes pointer from integer without a cast\n"
            "More build log\n")
        out_file = StringIO()
        self.assertEqual(1, filter_log(in_file, out_file, in_line=True))
        self.assertThat(out_file.getvalue(), MatchesRegex(
            r"^" +
            re.escape(
                "Build log with errors\n"
                "/build/gtk/ubuntumenuproxymodule.c:94: "
                "warning: implicit declaration of function 'foo'\n"
                "/build/gtk/ubuntumenuproxymodule.c:94: "
                "warning: assignment makes pointer from integer without a "
                "cast\n"
                "Function `foo' implicitly converted to pointer at "
                "/build/gtk/ubuntumenuproxymodule.c:94\n"
                "More build log\n"
                "Function `foo' implicitly converted to pointer at "
                "/build/gtk/ubuntumenuproxymodule.c:94\n\n\n\n") +
            r"Our automated build log filter.*",
            flags=re.M | re.S))
