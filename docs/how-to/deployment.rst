How to deploy launchpad-buildd
******************************

In Canonical's datacentre environments, launchpad-buildd is deployed as a
``.deb`` package installed in a fleet of VMs.  To upgrade it, we need to
rebuild the VM images.

Each environment uses its own PPA and management environment:

+--------------------------------------------------+--------------------------------------------------------------------------------------------------------------------+
| Environment                                      | PPA and management environment                                                                                     |
+==================================================+====================================================================================================================+
| `production <https://launchpad.net/builders>`_   | `ppa:launchpad/ubuntu/buildd <https://launchpad.net/~launchpad/+archive/ubuntu/buildd/+packages>`_                 |
|                                                  | ``prod-launchpad-vbuilders@is-bastion-ps5``                                                                        |
+--------------------------------------------------+--------------------------------------------------------------------------------------------------------------------+
| `dogfood <https://dogfood.paddev.net/builders>`_ | `ppa:launchpad/ubuntu/buildd-staging <https://launchpad.net/~launchpad/+archive/ubuntu/buildd-staging/+packages>`_ |
|                                                  | ``stg-vbuilder@launchpad-bastion-ps5``                                                                             |
+--------------------------------------------------+--------------------------------------------------------------------------------------------------------------------+

These instructions use various tools from `ubuntu-archive-tools
<https://git.launchpad.net/ubuntu-archive-tools>`_ (``copy-package`` and
``manage-builders``).

Testing on dogfood
------------------

#. Ensure everything has been merged to master.

#. Check that the `recipe
   <https://code.launchpad.net/~launchpad/+recipe/launchpad-buildd-daily>`_
   has built successfully (you can start a build manually if required), and
   that the resulting package has been published in the `Launchpad PPA
   <https://launchpad.net/~launchpad/+archive/ubuntu/ppa/+packages>`_.

#. Run ``copy-package --from=ppa:launchpad/ubuntu/ppa --suite=focal
   --to=ppa:launchpad/ubuntu/buildd-staging -b launchpad-buildd`` to copy
   the current version of launchpad-buildd to the deployment PPA.

#. `Wait for PPA publishing to complete
   <https://launchpad.net/~launchpad/+archive/ubuntu/buildd-staging/+packages>`_.

#. Run ``mojo run -m manifest-rebuild-images`` in the management environment
   (``stg-vbuilder@launchpad-bastion-ps5``) to start rebuilding images.
   After a minute or so, ``juju status glance-simplestreams-sync-\*`` will
   show "Synchronising images"; once this says "Sync completed", images have
   been rebuilt.

#. Builders will get the new image after they finish their next build (or
   are disabled) and go through being reset.  Since dogfood's build farm is
   typically mostly idle, you can use ``manage-builders -l dogfood --reset``
   to reset all builders and force them to pick up the new image.

#. Perform QA on dogfood until satisfied.

Releasing to production
-----------------------

#. Create a new release branch, e.g. ``release-213``, based on master.

#. Run ``DEBEMAIL="<email address>" DEBFULLNAME="<name>" dch -rD focal``.
   The later recipe build will prepend the correct preamble for each Ubuntu release.

#. Create a commit with a title like ``releasing package launchpad-buildd version 213``,
   push this branch and open a merge proposal with a title like
   ``Release version 213`` for review.

#. Once the release branch has merged to master,
   tag the release commit (e.g. ``git tag 213 && git push origin 213``).

#. Check that the `recipe
   <https://code.launchpad.net/~launchpad/+recipe/launchpad-buildd-daily>`_
   has built successfully (you can start a build manually if required), and
   that the resulting package has been published in the `Launchpad PPA
   <https://launchpad.net/~launchpad/+archive/ubuntu/ppa/+packages>`_.

#. Run ``copy-package --from=ppa:launchpad/ubuntu/ppa --suite=focal
   --to=ppa:launchpad/ubuntu/buildd -b launchpad-buildd`` to copy the
   current version of launchpad-buildd to the deployment PPA.

#. `Wait for PPA publishing to complete
   <https://launchpad.net/~launchpad/+archive/ubuntu/buildd/+packages>`_.

#. File an RT ticket asking IS to run ``mojo run -m
   manifest-rebuild-images`` in the management environment
   (``prod-launchpad-vbuilders@is-bastion-ps5``) to start rebuilding images.
   (`cRT#151858 <https://portal.admin.canonical.com/C151858>`_ will allow
   this step to be self-service.)

#. Once image builds complete, builders will get the new image after they
   finish their next build (or are disabled) and go through being reset.
   `Build farm administrators
   <https://launchpad.net/~launchpad-buildd-admins/+members>`_ can use
   ``manage-builders --virt --idle --reset`` to reset idle builders.

#. Close any bugs fixed by the new release.
