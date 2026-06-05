#!/usr/bin/python3

import sys
import os

sys.path.append("../")
import tools.encoder

# server
sourceServer = 'http://intel1:3000/'

# CT SPORT
vdrId = 'T-8395-8465-274'

os.sched_setaffinity(0, {4,5,6,7,8,9,10,11})
enc = tools.encoder.EncoderPipeline(sourceServer, vdrId, 2, 4, 5, '', 'H264', 50)
enc.run()
