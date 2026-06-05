#!/usr/bin/python3

import sys
import os

sys.path.append("../")
import tools.encoder

# server
sourceServer = 'http://intel1:3000/'

# NOVA FUN
vdrId = 'T-8395-1040-517'

os.sched_setaffinity(0, {28,29,30,31})
enc = tools.encoder.EncoderPipeline(sourceServer, vdrId, 2, 2, 7, '', 'H264', 50)
enc.run()
