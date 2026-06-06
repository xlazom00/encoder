#!/usr/bin/python3

import sys
import os

sys.path.append("../")
import tools.encoder

# server
sourceServer = 'http://intel1:3000/'

# CT2 HD
vdrId = 'T-8395-8465-270'

os.sched_setaffinity(0, {8,9,10,11,12,13,14,15})
enc = tools.encoder.EncoderPipeline(sourceServer, vdrId, 4, 4, 6, '', 'H264', 50)

enc.run()
