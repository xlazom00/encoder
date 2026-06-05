#!/usr/bin/python3

import sys
import os
import socket

sys.path.append("../")

import tools.encoder71

# PRIMA
vdrId = 'T-8395-61958-786'

hc = tools.encoder71.HealthCheck(vdrId)
hc.check()
