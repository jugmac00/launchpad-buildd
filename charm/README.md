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

You can either deploy the stock launchpad-buildd package from a PPA, or
build your own.

Installing from a PPA is the default; just deploy this charm.

If you're building your own package, then you already have the
launchpad-buildd code checked out.  In the `charm/` subdirectory, build the
charm and packages together:

```
make build-with-packages
```

Then deploy the charm, attaching the packages as resources:

```
make deploy-with-packages
```

Either way, this should eventually give you a running builder.  Find out its
host name (e.g. `juju-XXXXXX-0.lxd`) and [add it to your local Launchpad
instance](https://launchpad.dev/builders/+new) (e.g.
`http://juju-XXXXXX-0.lxd:8221/`).

# Notes

This charm gives you a non-virtualized builder, since there is no reset from
a base image between builds; you'll need to make sure that any archives or
snaps with builds you intend to dispatch to this builder have the "Require
virtualized builders" option disabled.

The Launchpad development wiki has [instructions on setting up the rest of
Launchpad](https://dev.launchpad.net/Soyuz/HowToUseSoyuzLocally).
You can skip the parts about installing the builder.
