# Copyright 2009-2011 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

# Buildd Slave implementation
# XXX: dsilvers: 2005/01/21: Currently everything logged in the slave gets
# passed through to the twistd log too. this could get dangerous/big

try:
    from configparser import ConfigParser as SafeConfigParser
except ImportError:
    from ConfigParser import SafeConfigParser
import os

from twisted.application import (
    service,
    strports,
    )
from twisted.scripts.twistd import ServerOptions
from twisted.web import (
    resource,
    server,
    static,
    )

from lpbuildd.binarypackage import BinaryPackageBuildManager
from lpbuildd.livefs import LiveFilesystemBuildManager
from lpbuildd.log import RotatableFileLogObserver
from lpbuildd.slave import XMLRPCBuildDSlave
from lpbuildd.snap import SnapBuildManager
from lpbuildd.sourcepackagerecipe import SourcePackageRecipeBuildManager
from lpbuildd.translationtemplates import TranslationTemplatesBuildManager


options = ServerOptions()
options.parseOptions()

conffile = os.environ.get('BUILDD_SLAVE_CONFIG', 'buildd-slave-example.conf')

conf = SafeConfigParser()
conf.read(conffile)
slave = XMLRPCBuildDSlave(conf)

slave.registerBuilder(BinaryPackageBuildManager, "binarypackage")
slave.registerBuilder(SourcePackageRecipeBuildManager, "sourcepackagerecipe")
slave.registerBuilder(
    TranslationTemplatesBuildManager, 'translation-templates')
slave.registerBuilder(LiveFilesystemBuildManager, "livefs")
slave.registerBuilder(SnapBuildManager, "snap")

application = service.Application('BuildDSlave')
application.addComponent(
    RotatableFileLogObserver(options.get('logfile')), ignoreClass=1)
builddslaveService = service.IServiceCollection(application)

root = resource.Resource()
root.putChild('rpc', slave)
root.putChild('filecache', static.File(conf.get('slave', 'filecache')))
slavesite = server.Site(root)

strports.service("tcp:%s" % slave.slave._config.get("slave", "bindport"),
                 slavesite).setServiceParent(builddslaveService)

# You can interact with a running slave like this:
# (assuming the slave is on localhost:8221)
#
# python3
# from xmlrpc.client import ServerProxy
# s = ServerProxy("http://localhost:8221/rpc")
# s.echo("Hello World")
