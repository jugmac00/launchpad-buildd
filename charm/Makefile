NAME ?= launchpad-buildd
TOPDIR ?= $(CURDIR)/..
BUILDDIR ?= $(CURDIR)/dist
TMPDIR ?= $(CURDIR)/tmp

CHARM_SERIES ?= bionic
CHARM_SRC ?= $(CURDIR)
JUJU_REPOSITORY = $(BUILDDIR)
CHARMDIR = $(BUILDDIR)/$(CHARM_SERIES)/$(NAME)
CHARMREPODIR = $(BUILDDIR)/build
CHARM = $(CHARMDIR)/.done
LAYER_PATH = $(TMPDIR)/layer
INTERFACE_PATH = $(TMPDIR)/interface
EXTRA_CHARM_BUILD_ARGS ?=
DEPLOY_ENV ?= devel
CHARM_STORE_URL ?= cs:~launchpad/launchpad-buildd

export INTERFACE_PATH
export LAYER_PATH
export JUJU_REPOSITORY


build: $(CHARM)

$(BUILDDIR):
	mkdir -p $@

$(CHARM): $(CHARM_SRC) | $(BUILDDIR)
	rm -rf $(CHARMDIR)
	charm build -o $(BUILDDIR) -s $(CHARM_SERIES) -n $(NAME) $(EXTRA_CHARM_BUILD_ARGS)
	touch $@

clean:
	rm -rf $(BUILDDIR) $(TMPDIR)

create-privileged-model:
	juju add-model privileged localhost
	lxc profile set juju-privileged security.privileged true

deploy:
	juju deploy $(CHARMDIR)

push:
	charm push $(CHARMDIR) $(CHARM_STORE_URL)

.PHONY: build clean create-privileged-model deploy push
