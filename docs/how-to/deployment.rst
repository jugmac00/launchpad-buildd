How to deploy launchpad-buildd
******************************

In Canonical's datacentre environments, launchpad-buildd is deployed as a
``.deb`` package installed in a fleet of VMs.  To upgrade it, we need to
rebuild the VM images.

Each environment uses its own PPA and management environment:

+---------------------------------------------------------+--------------------------------------------------------------------------------------------------------------------+
| Environment                                             | PPA and management environment                                                                                     |
+=========================================================+====================================================================================================================+
| `production <https://launchpad.net/builders>`_          | `ppa:launchpad/ubuntu/buildd <https://launchpad.net/~launchpad/+archive/ubuntu/buildd/+packages>`_                 |
|                                                         | ``prod-launchpad-vbuilders@is-bastion-ps5``                                                                        |
+---------------------------------------------------------+--------------------------------------------------------------------------------------------------------------------+
| `qastaging <https://qastaging.launchpad.net/builders>`_ | `ppa:launchpad/ubuntu/buildd-staging <https://launchpad.net/~launchpad/+archive/ubuntu/buildd-staging/+packages>`_ |
|                                                         | ``stg-vbuilder-qastaging@launchpad-bastion-ps5``                                                                   |
+---------------------------------------------------------+--------------------------------------------------------------------------------------------------------------------+

These instructions use various tools from `ubuntu-archive-tools
<https://git.launchpad.net/ubuntu-archive-tools>`_ (``copy-package`` and
``manage-builders``).

Testing on qastaging
--------------------

#. Ensure everything has been merged to master.

#. Check that the `recipe
   <https://code.launchpad.net/~launchpad/+recipe/launchpad-buildd-daily>`_
   has built successfully (you can start a build manually if required), and
   that the resulting package has been published in the `Launchpad PPA
   <https://launchpad.net/~launchpad/+archive/ubuntu/ppa/+packages>`_.

#. Run ``copy-package --from=ppa:launchpad/ubuntu/ppa --suite=jammy
   --to=ppa:launchpad/ubuntu/buildd-staging -b launchpad-buildd``
   (from ``ubuntu-archive-tools``) to copy the current version of launchpad-buildd
   to the deployment PPA (``jammy`` here refers to the series being used on
   qastaging builder instances).

#. `Wait for PPA publishing to complete
   <https://launchpad.net/~launchpad/+archive/ubuntu/buildd-staging/+packages>`__.

#. Run ``mojo run -m manifest-rebuild-images`` in the management environment
   (``stg-vbuilder-qastaging@launchpad-bastion-ps5``) to start rebuilding images.
   After a minute or so, ``juju status glance-simplestreams-sync-\*`` will
   show "Synchronising images"; once this says "Sync completed", images have
   been rebuilt.

   Note that if ``mojo run -m manifest-rebuild-images`` fails, run ``mojo run``
   instead.

   .. note::
      Some glance-simplestreams-sync units may be in an unknown state:
      as a consequence, the images that we have in OpenStack for the 
      affected units are not updated. This will cause an `error
      <https://pastebin.canonical.com/p/ChfGwsQNGJ/>`_ 
      when you try to rebuild images blocking the execution of the script.
      This doesn't happen using `mojo run`.

#. Builders will get the new image after they finish their next build (or
   are disabled) and go through being reset.  Since qastaging's build farm
   is typically mostly idle, you can use ``manage-builders -l qastaging
   --reset`` to reset all builders and force them to pick up the new image
   (from ``ubuntu-archive-tools``).

#. Perform QA on qastaging until satisfied, see :doc:`/how-to/qa`.

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
   <https://launchpad.net/~launchpad/+archive/ubuntu/buildd/+packages>`__.

#. Run ``ssh prod-launchpad-vbuilders@is-bastion-ps5.internal
   /home/prod-launchpad-vbuilders/scripts/rebuild-images.sh`` from the
   staging management environment (``stg-vbuilder@launchpad-bastion-ps5``)
   to start rebuilding images.

#. Once the new image is rebuilt, which normally takes on the order of 15-60
   minutes depending on the architecture, builders will get the new image
   after they finish their next build (or are disabled) and go through being
   reset.  As a result, ``manage-builders -v`` should start showing the new
   version over time.

#. Wait for the new version to appear for at least one builder in each
   region and architecture.  If this doesn't happen after 90 minutes, then
   ask IS for assistance in investigating; they can start by checking ``juju
   status`` in ``prod-launchpad-vbuilders@is-bastion-ps5.internal``.

#. Once the updated version is visible for at least one builder in each
   region and architecture, `build farm administrators
   <https://launchpad.net/~launchpad-buildd-admins/+members>`_ can use
   ``manage-builders --virt --idle --builder-version=<old-version> --reset``
   to reset idle builders, thereby causing builders that haven't taken any
   builds recently to catch up.

#. Close any bugs fixed by the new release.
