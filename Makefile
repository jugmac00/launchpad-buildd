# Copyright 2009 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

all: deb

src: clean
	dpkg-buildpackage -rfakeroot -uc -us -S

deb: clean
	dpkg-buildpackage -rfakeroot -uc -us

clean:
	fakeroot debian/rules clean

realclean:
	rm -f ../launchpad-buildd*tar.gz
	rm -f ../launchpad-buildd*dsc
	rm -f ../launchpad-buildd*deb
	rm -f ../launchpad-buildd*changes

.PHONY: all clean deb

PYTHON=python
check:
	PYTHONPATH=$(CURDIR):$(PYTHONPATH) $(PYTHON) -m testtools.run -v \
		   lpbuildd.pottery.tests.test_generate_translation_templates \
		   lpbuildd.pottery.tests.test_intltool \
		   lpbuildd.tests.test_binarypackage \
		   lpbuildd.tests.test_buildd_slave \
		   lpbuildd.tests.test_buildrecipe \
		   lpbuildd.tests.test_check_implicit_pointer_functions \
		   lpbuildd.tests.test_debian \
		   lpbuildd.tests.test_harness \
		   lpbuildd.tests.test_livefs \
		   lpbuildd.tests.test_snap \
		   lpbuildd.tests.test_sourcepackagerecipe \
		   lpbuildd.tests.test_translationtemplatesbuildmanager
