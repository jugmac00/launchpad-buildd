# Overview

This charm installs a Launchpad builder, which can build packages in
response to requests from a Launchpad instance.  It is mainly intended for
use by Launchpad developers testing changes to builder handling.

# Setup

Builders need to be able to unpack chroots, which involves being able to
create device nodes.  Unprivileged LXD containers cannot do this.  If you
want to use this with the LXD provider, you should therefore do this first:

```
make create-privileged-model
```

... or, if you need more control, some variation on this:

```
juju add-model privileged localhost
lxc profile set juju-privileged security.privileged true
```

# Deployment

```
make deploy
```

This charm will deploy the launchpad-buildd package from a PPA.  If you want
to deploy a modified version of launchpad-buildd, you can either build it
locally and install the resulting packages manually after initial
deployment, or you can upload a modified source package to your own PPA and
set `install_sources` to refer to that PPA.

Either way, this should eventually give you a running builder.  Find out its
host name (e.g. `juju-XXXXXX-0.lxd`) and [add it to your local Launchpad
instance](https://launchpad.test/builders/+new) (e.g.
`http://juju-XXXXXX-0.lxd:8221/`).

# Notes

This charm gives you a non-virtualized builder, since there is no reset from
a base image between builds; you'll need to make sure that any archives or
snaps with builds you intend to dispatch to this builder have the "Require
virtualized builders" option disabled.

The Launchpad development wiki has [instructions on setting up the rest of
Launchpad](https://dev.launchpad.net/Soyuz/HowToUseSoyuzLocally).
You can skip the parts about installing the builder.
