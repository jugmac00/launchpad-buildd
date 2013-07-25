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
# NB: for this to pass, you must have txfixtures, lazr.restful, and lp on your pythonpath
# already.  lp is not packaged so this is not enforced as a build time
# dependency. In practice you probably just want to run this with PYTHON=bin/py from 
# a Launchpad checkout.
check:
	PYTHONPATH=$(PWD):$(PYTHONPATH) $(PYTHON) -m testtools.run -v \
		   lpbuildd.tests.test_binarypackage \
		   lpbuildd.tests.test_buildd_slave \
		   lpbuildd.tests.test_check_implicit_pointer_functions \
		   lpbuildd.tests.test_harness \
		   lpbuildd.tests.test_translationtemplatesbuildmanager
