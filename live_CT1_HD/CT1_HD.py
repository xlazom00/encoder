#!/usr/bin/python3

import sys
import os

sys.path.append("../")
import tools.encoder

# server
sourceServer = 'http://intel1:3000/'

# CT1 HD
vdrId = 'T-8395-8465-268'

os.sched_setaffinity(0, {0,1,2,3,4,5,6,7})
enc = tools.encoder.EncoderPipeline(sourceServer, vdrId, 4, 4, 6, '', 'H264', 50)

enc.run()
