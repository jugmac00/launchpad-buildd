How to set up a development environment
***************************************

First of all, it is recommended that you create an lxc container, since the
following steps will make changes in your system. And since some build types
will only work with virtualized containers, creating an lxc vm is the best way
to go. If you just want to run the test suite, creating a container is
sufficient.

You can create a container with the following command:

.. code:: bash

        lxc launch --vm ubuntu:20.04 lp-builddev

Note that you may want to have a profile to share the source code with the
container before running the above command.

Then, inside the container, install the necessary dependencies:

.. code:: bash

        sudo add-apt-repository ppa:launchpad/ppa
        sudo apt-get update
        cat system-dependencies.txt | sudo xargs apt-get install -y

This should be enough for you to be able to run the test suite via `make
check`.

More information on how to integrate it with Launchpad can be found here:
https://dev.launchpad.net/Soyuz/HowToDevelopWithBuildd

