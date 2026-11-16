"""Entry point of the build's host-repair runner. startup.py launches this file as its own script (RunScript);
the work is in resources/libs/host_repairs/flow/runner.py."""
from resources.libs.host_repairs.flow import runner

runner.main()
