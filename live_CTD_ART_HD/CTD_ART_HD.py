#!/usr/bin/python3

import sys
import os

sys.path.append("../")
import tools.encoder

# server
sourceServer = 'http://intel1:3000/'

# CT D/ART
vdrId = 'T-8395-8465-276'

os.sched_setaffinity(0, {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15})
enc = tools.encoder.EncoderPipeline(sourceServer, vdrId, 2, 4, 6, '', 'H264', 50)

enc.run()
