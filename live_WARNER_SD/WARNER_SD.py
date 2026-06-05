#!/usr/bin/python3

import sys
import os

sys.path.append("../")
import tools.encoder

# server
sourceServer = 'http://intel1:3000/'

# WARNER
vdrId = 'T-8395-61959-8479'

os.sched_setaffinity(0, {16,17,18,19,20,21,22,23,24,25})
enc = tools.encoder.EncoderPipeline(sourceServer, vdrId, 2, 2, 7, '', 'H264', 50)
enc.run()
