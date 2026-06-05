#!/usr/bin/python3

import time
import sqlobject
from sqlobject import *
import MySQLdb.converters
import sys, os
import math
from datetime import datetime

import gi

gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst
#, GstMpegts

GObject.threads_init()
Gst.init(None)

videoBitrate0 =  4200
videoKeyFrameInterval0 = 250
videoBitrate1 =   200
videoKeyFrameInterval1 = 250
videoBitrate2 =   10
videoKeyFrameInterval2 = 25
#audioBitrate =    69536
audioBitrate =    91920

# in ms
appStartTime = int(round(time.time() * 1000))
defaultSegmentDuration = 10000000000 #10s segments
defaultMissingSegments = 2

#lastSegmentEndTime = 0

timeStamps = [0,0,0]
invalidTests = 0

encoders = [1,0,0]
withDeinterlace = 0
#topx264speed = 4

#threads = 4

###### for dump dot
###### export GST_DEBUG_DUMP_DOT_DIR=""

def _mysql_timestamp_converter(raw):
	"""Convert a MySQL TIMESTAMP to a floating point number representing
	the seconds since the Un*x Epoch. It uses custom code the input seems
	to be the new (MySQL 4.1+) timestamp format, otherwise code from the
	MySQLdb module is used."""
	if raw[4] == '-':
		return time.mktime(time.strptime(raw, '%Y-%m-%d %H:%M:%S'))
	else:
		return MySQLdb.converters.mysql_timestamp_converter(raw)


conversions = MySQLdb.converters.conversions.copy()
conversions[MySQLdb.constants.FIELD_TYPE.TIMESTAMP] = _mysql_timestamp_converter

MySQLConnection = sqlobject.mysql.builder()
connection = MySQLConnection(host='intel1', user='streamer2', password='streamer', db='streamer', conv=conversions, debug='false')
sqlhub.processConnection = connection

class Channel(SQLObject):
    vdrId = StringCol()
    name = StringCol()


class FileSegment(SQLObject):
    fileId = BigIntCol()
    startTime = BigIntCol()
    duration = IntCol()
    channel = ForeignKey('Channel')
    firstLastIndex = DatabaseIndex('fileId', 'channel')

class HealthCheck:
	def __init__(self, pChannelVdrId):
		self.channelVdrId = pChannelVdrId
	def check(self):
		currentChannel = Channel.select(Channel.q.vdrId == self.channelVdrId)
		self.currentChannel = (list(currentChannel))[0]
		print(self.currentChannel)
		res = FileSegment.select( "file_segment.id = (SELECT MAX( file_segment.id ) FROM file_segment WHERE channel_id=%s )" % self.currentChannel.id)
		result = list(res)
		if (len(result) == 0):
			return 0
		
		missingSegments = math.floor((appStartTime - (result[0].startTime + (defaultSegmentDuration / 1000000))) / (defaultSegmentDuration / 1000000))
		
		print('last file in table:', result[0].fileId)
		print(appStartTime - (result[0].startTime + (defaultSegmentDuration / 1000000)))
		print(missingSegments)
		
		if (missingSegments>defaultMissingSegments):
			sys.exit(1)

class OutPut(Gst.Bin):
	def __init__(self, startFileIndex, outputDirectory, index):
		Gst.Bin.__init__(self)
		self.startFileId = startFileIndex
		self.fileId = startFileIndex

		self.mux = Gst.ElementFactory.make('mpegtsmux', None)
		self.sink = Gst.ElementFactory.make('splitmuxsink', 'splitmuxsink' + index)

		self.add(self.sink)
		self.sink.set_property('start-index', startFileIndex)
		self.sink.set_property('location', outputDirectory)
		self.sink.set_property('max-files', 0)
		self.sink.set_property('max-size-time', defaultSegmentDuration) #10s segments
		self.sink.set_property('muxer', self.mux)
		self.sink.set_property('send-keyframe-requests', 'true')

		self.mux.set_property('pat-interval', 900000)
		self.mux.set_property('pmt-interval', 900000)

class AudioEncoder(Gst.Bin):
	def __init__(self, bitrate):
		super(Gst.Bin, self).__init__()
		self.audioconvert = Gst.ElementFactory.make('audioconvert', None)
		self.encoder = Gst.ElementFactory.make('fdkaacenc', None)
		self.encoder.set_property('bitrate', bitrate)  # tools bitrate


		self.aacparse = Gst.ElementFactory.make('aacparse', None)
		self.aacparse.set_property('disable-passthrough', 'true')

		self.qOut = Gst.ElementFactory.make('queue', None)

		self.add(self.audioconvert)
		self.add(self.encoder)
		self.add(self.aacparse)
		self.add(self.qOut)

		self.audioconvert.link(self.encoder)
		self.encoder.link_filtered(self.aacparse,
			Gst.caps_from_string('audio/mpeg, stream-format=adts')
		)

		self.aacparse.link_filtered(self.qOut,
			Gst.caps_from_string('audio/mpeg, stream-format=adts')
		)

		# Add Ghost Pads
		self.add_pad(
			Gst.GhostPad.new('sink', self.audioconvert.get_static_pad('sink'))
		)

		self.add_pad(
			Gst.GhostPad.new('src', self.qOut.get_static_pad('src'))
		)


class VideoEncoder(Gst.Bin):
	def __init__(self, codec, bitrate, keyFrameInterval, encoderThreads, encoderSpeed):
		super(Gst.Bin, self).__init__()
		if codec == 'H264':
			self.initH264(bitrate, keyFrameInterval, encoderThreads, encoderSpeed)
		elif codec == 'H265':
			self.initH265(bitrate, keyFrameInterval, encoderThreads, encoderSpeed)

	def initH264(self, bitrate, keyFrameInterval, encoderThreads, encoderSpeed):
		# Create elements
		self.inQ = Gst.ElementFactory.make('queue2', None)
		self.encoder = Gst.ElementFactory.make('x264enc', None)
		self.parser = Gst.ElementFactory.make('h264parse', None)
		self.qOut = Gst.ElementFactory.make('queue2', None)

		# Add elements to Bin
		self.add(self.inQ)
		self.add(self.encoder)
		self.add(self.parser)
		self.add(self.qOut)

		self.inQ.set_property('max-size-buffers', 800)
		self.inQ.set_property('max-size-bytes', 0)
		self.qOut.set_property('max-size-bytes', 0)

		# Set tools properties
		opt_string = 'bitrate=' + str(bitrate)
		self.encoder.set_property('bitrate', bitrate)  # video tools bitrate

		if(keyFrameInterval < 100):
			opt_string += (':aud:scenecut=2:rc-lookahead=2:threads=1:vbv-maxrate='+str(bitrate)+':vbv-bufsize='+str(bitrate*2)+':keyint='+str(keyFrameInterval)+':min-keyint='+str(keyFrameInterval)+':scenecut=0:no-scenecut')
#			self.encoder.set_property('vbv-buf-capacity', 1000) 
			self.encoder.set_property('tune', 1)
			self.encoder.set_property('speed-preset', 5)
		elif(bitrate < 500):
			opt_string += (':threads='+str(2)+':aud:vbv-maxrate='+str(bitrate)+':vbv-bufsize='+str(bitrate*2)+':keyint='+str(keyFrameInterval)+':min-keyint='+str(keyFrameInterval)+':scenecut=0:no-scenecut')
			self.encoder.set_property('psy-tune', 0)
			self.encoder.set_property('speed-preset', 5)
		else:
			opt_string += (':threads='+str(encoderThreads)+':aud:vbv-maxrate='+str(bitrate)+':vbv-bufsize='+str(bitrate*2)+':keyint='+str(keyFrameInterval)+':min-keyint=50')
			self.encoder.set_property('speed-preset', encoderSpeed)
			self.encoder.set_property('psy-tune', 4)

		print(opt_string)
		self.encoder.set_property('option-string',opt_string)

		self.inQ.link(self.encoder)

		self.encoder.link_filtered(self.parser,
			Gst.caps_from_string('video/x-h264, profile=high')
		)

		self.parser.link_filtered(self.qOut,
			Gst.caps_from_string('video/x-h264, alignment=au, stream-format=byte-stream')
		)

		# Add Ghost Pads
		self.add_pad(
			Gst.GhostPad.new('sink', self.inQ.get_static_pad('sink'))
		)

		self.add_pad(
			Gst.GhostPad.new('src', self.qOut.get_static_pad('src'))
		)

		Gst.debug_bin_to_dot_file( self, Gst.DebugGraphDetails.ALL, "videoenc")

	def initH265(self, bitrate, keyFrameInterval, encoderThreads, encoderSpeed):
		# Create elements
		self.inQ = Gst.ElementFactory.make('queue2', None)
		self.encoder = Gst.ElementFactory.make('x265enc', None)
		self.parser = Gst.ElementFactory.make('h265parse', None)
		self.qOut = Gst.ElementFactory.make('queue2', None)

		# Add elements to Bin
		self.add(self.inQ)
		self.add(self.encoder)
		self.add(self.parser)
		self.add(self.qOut)

		self.inQ.set_property('max-size-buffers', 800)
		self.inQ.set_property('max-size-bytes', 0)
		self.qOut.set_property('max-size-bytes', 0)

		# Set tools properties
		opt_string = 'bitrate=' + str(bitrate)
		self.encoder.set_property('bitrate', bitrate)  # video tools bitrate

		if(keyFrameInterval < 100):
			opt_string += (':aud:scenecut=2:rc-lookahead=2:threads=1:vbv-maxrate='+str(bitrate)+':vbv-bufsize='+str(bitrate*2)+':keyint='+str(keyFrameInterval)+':min-keyint='+str(keyFrameInterval)+':scenecut=0:no-scenecut')
#			self.encoder.set_property('vbv-buf-capacity', 1000) 
			self.encoder.set_property('tune', 1)
			self.encoder.set_property('speed-preset', 5)
		elif(bitrate < 500):
			opt_string += (':threads='+str(2)+':aud:vbv-maxrate='+str(bitrate)+':vbv-bufsize='+str(bitrate*2)+':keyint='+str(keyFrameInterval)+':min-keyint='+str(keyFrameInterval)+':scenecut=0:no-scenecut')
			self.encoder.set_property('psy-tune', 0)
			self.encoder.set_property('speed-preset', 5)
		else:
			opt_string += (':pools='+str(encoderThreads)+':aud:vbv-maxrate='+str(bitrate)+':vbv-bufsize='+str(bitrate*2)+':keyint='+str(keyFrameInterval)+':min-keyint=50')
			opt_string += (':asm=avx512')
			# ':frame-threads='+str(encoderThreads)+
			self.encoder.set_property('speed-preset', encoderSpeed)

		
		self.encoder.set_property('log-level', 2)
		print(opt_string)
		self.encoder.set_property('option-string',opt_string)

		self.inQ.link(self.encoder)

		self.encoder.link_filtered(self.parser,
			Gst.caps_from_string('video/x-h265, profile=main')
		)

		self.parser.link_filtered(self.qOut,
			Gst.caps_from_string('video/x-h265, alignment=au, stream-format=byte-stream')
		)

		# Add Ghost Pads
		self.add_pad(
			Gst.GhostPad.new('sink', self.inQ.get_static_pad('sink'))
		)

		self.add_pad(
			Gst.GhostPad.new('src', self.qOut.get_static_pad('src'))
		)

		Gst.debug_bin_to_dot_file( self, Gst.DebugGraphDetails.ALL, "videoenc")


class EncoderPipeline:
	def __init__(self, sourceServer, channelVdrId, decoderThreads, encoderThreads, encoderSpeed, targetResolution, videoCodec='H264', targetFPS=25):
		self.uri = sourceServer + channelVdrId

		(startfileindex, currentchannelid) = self.getchannelinfo(channelVdrId)
		self.startFileIndex = startfileindex
		#self.startFileIndex = 1
		#currentchannelid = 256

		# destination location
		self.destination0 = self.formatdestination(currentchannelid, '100')
		self.destination1 = self.formatdestination(currentchannelid, '030')
		self.destination2 = self.formatdestination(currentchannelid, '005')

		# create destination directory
		self.destinationdirectory0 = self.formatdestinationdir(currentchannelid, '100')
		self.destinationdirectory1 = self.formatdestinationdir(currentchannelid, '030')
		self.destinationdirectory2 = self.formatdestinationdir(currentchannelid, '005')
		try:
			os.makedirs(self.destinationdirectory0)
		except OSError:
			print(self.destinationdirectory0 + ' exist')
			pass
		try:
			os.makedirs(self.destinationdirectory1)
		except OSError:
			print(self.destinationdirectory1 + ' exist')
			pass
		try:
			os.makedirs(self.destinationdirectory2)
		except OSError:
			print(self.destinationdirectory2 + ' exist')
			pass

		self.lastSegmentTimeStamp = None
		self.lastSegmentTimeStampTested = 0

		# print destination

		# clean table records
		# FileSegment.dropTable()
		# FileSegment.createTable()
		#

		# return

		self.mainloop = GObject.MainLoop()
		self.pipeline = Gst.Pipeline()
		self.bus = self.pipeline.get_bus()

		self.bus.add_signal_watch()
		self.bus.enable_sync_message_emission()

		# self.bus.connect("sync-message::element", self.on_sync_message)
		self.bus.connect("message", self.on_message)

		self.bus.connect('message::eos', self.on_eos)
		self.bus.connect('message::error', self.on_error)
		#         self.bus.connect('message::eos', self.on_eos)

		# Create elements
		self.player = Gst.ElementFactory.make('uridecodebin3', None)
		caps = Gst.Caps.from_string("video/x-h265; audio/mpeg; audio/x-ac3; text/x-raw")
		self.player.set_property('caps', caps)

		self.player.set_property('uri', self.uri + ".ts")
		self.player.set_property('use-buffering', 'true')
		#self.player.set_property('force-sw-decoders', 'true')
		self.player.set_property('buffer-size', 620000000)


		self.videoParseDecoder = Gst.ElementFactory.make('h265parse', None)
		self.videoDecoder = Gst.ElementFactory.make('avdec_h265', None)
		self.videoDecoder.set_property('max-threads', decoderThreads);
		self.videoDecoder.set_property('output-corrupt', 'true');

		self.pipeline.add(self.videoParseDecoder)
		self.pipeline.add(self.videoDecoder)

		if(withDeinterlace):
			#self.videoDeinterlace = Gst.ElementFactory.make('avdeinterlace')
			self.videoDeinterlace = Gst.ElementFactory.make('yadif')
			self.videoDeinterlace.set_property('mode',1)

		self.inputVideoRate = Gst.ElementFactory.make('videorate', None)
		self.inputAudioRate = Gst.ElementFactory.make('audiorate', None)

		self.audioParseDecoder = Gst.ElementFactory.make('aacparse', None)
		self.audioDecoder = Gst.ElementFactory.make('avdec_aac_latm', None)

		self.videoDecTee = Gst.ElementFactory.make('tee', None)
		self.audioDecTee = Gst.ElementFactory.make('tee', None)

		self.encAudioTee = Gst.ElementFactory.make('tee', None)

		self.audioEncoder = AudioEncoder(audioBitrate)

		# Add elements to pipeline
		self.pipeline.add(self.player)
		self.pipeline.add(self.audioParseDecoder)
		self.pipeline.add(self.audioDecoder)
		self.pipeline.add(self.audioEncoder)

		self.pipeline.add(self.inputAudioRate)
		self.pipeline.add(self.inputVideoRate)

		if(withDeinterlace):
			self.pipeline.add(self.videoDeinterlace)

		self.pipeline.add(self.videoDecTee)
		self.pipeline.add(self.audioDecTee)
		self.pipeline.add(self.encAudioTee)


		#	self.hole = Gst.ElementFactory.make('audiotestsrc', None)
		#	self.hole.set_property('is-live', 'true');
		#	self.hole.set_property('wave', 'silence');
		#	
		#	self.holeAudioEncoder = AudioEncoder(100)

		if(encoders[2]):
			self.videoRate2 = Gst.ElementFactory.make('videorate', None)
			self.videoScale2 = Gst.ElementFactory.make('videoscale', None)
			self.videoScale2.set_property('method', 3)

			self.videoEncoder2 = VideoEncoder(videoBitrate2, videoKeyFrameInterval2)
			self.outPut2 = OutPut(self.startFileIndex, self.destination2, "2")
			self.audioQueue2 = Gst.ElementFactory.make('queue2', None)

			#        self.pipeline.add(self.identity2)
			self.pipeline.add(self.videoRate2)
			self.pipeline.add(self.videoScale2)
			self.pipeline.add(self.videoEncoder2)
			self.pipeline.add(self.audioQueue2)
			self.pipeline.add(self.outPut2)

		#        self.pipeline.add(self.hole)
		#        self.pipeline.add(self.holeAudioEncoder)


		if(encoders[1]):
			self.videoScale1 = Gst.ElementFactory.make('videoscale', None)
			self.videoScale1.set_property('method', 3)

			self.videoEncoder1 = VideoEncoder(videoBitrate1, videoKeyFrameInterval1)
			self.outPut1 = OutPut(self.startFileIndex, self.destination1, "1")
			self.audioQueue1 = Gst.ElementFactory.make('queue2', None)

			self.pipeline.add(self.videoScale1)
			self.pipeline.add(self.videoEncoder1)
			self.pipeline.add(self.audioQueue1)
			self.pipeline.add(self.outPut1)


		if(encoders[0]):
			#self.videoScale0 = Gst.ElementFactory.make('videoscale', None)
			#self.videoScale0.set_property('method', 3)
			if(targetFPS == 25):
				self.videoEncoder0 = VideoEncoder(videoCodec, videoBitrate0, videoKeyFrameInterval0, encoderThreads, encoderSpeed)
			else:
				self.videoEncoder0 = VideoEncoder(videoCodec, videoBitrate0, 2*videoKeyFrameInterval0, encoderThreads, encoderSpeed)
			self.outPut0 = OutPut(self.startFileIndex, self.destination0, "0")
			self.audioQueue0 = Gst.ElementFactory.make('queue2', None)

			#self.pipeline.add(self.videoScale0)
			self.pipeline.add(self.videoEncoder0)
			self.pipeline.add(self.audioQueue0)
			self.pipeline.add(self.outPut0)


		self.audioLinked = False

		# Connect signal handlers
		self.player.connect('pad-added', self.on_pad_added)

		# video decoder
		self.videoParseDecoder.link(self.videoDecoder)
		if(withDeinterlace):
			self.videoDecoder.link(self.videoDeinterlace)
			self.videoDeinterlace.link(self.inputVideoRate)
		else:
			self.videoDecoder.link(self.inputVideoRate)

		if(targetFPS == 25):
			self.inputVideoRate.link_filtered(self.videoDecTee,
				Gst.caps_from_string('video/x-raw, framerate=25/1')
			)
		else:
			self.inputVideoRate.link_filtered(self.videoDecTee,
				Gst.caps_from_string('video/x-raw, framerate=50/1')
			)

		if(encoders[2]):
			# quality 2
			#	self.videoDecTee.link(self.identity2)
			self.videoDecTee.link(self.videoRate2)
			#        self.identity2.link(self.videoRate2)
			self.videoRate2.link_filtered(self.videoScale2,
				Gst.caps_from_string('video/x-raw, framerate=25/10')
			)
			self.videoScale2.link_filtered(self.videoEncoder2,
				Gst.caps_from_string('video/x-raw, width=180, height=144')
			)

			self.videoEncoder2.link(self.outPut2.sink)

		if(encoders[1]):
			# quality 1
			self.videoDecTee.link(self.videoScale1)
			self.videoScale1.link_filtered(self.videoEncoder1,
				Gst.caps_from_string('video/x-raw, width=360, height=288')
			)
			self.videoEncoder1.link(self.outPut1.sink)


		if(encoders[0]):
			# quality 0
			self.videoDecTee.link(self.videoEncoder0)
			#self.videoDecTee.link(self.videoScale0)
			#self.videoScale0.link_filtered(self.videoEncoder0,
			#	Gst.caps_from_string('video/x-raw, width=1280, height=720')
			#)
			self.videoEncoder0.link(self.outPut0.sink)

		# audio decoder
		self.audioParseDecoder.link(self.audioDecoder)

		# noaudiorate
		#self.audioDecoder.link(self.audioDecTee)

		# audiorate
		self.audioDecoder.link(self.inputAudioRate)
		#self.inputAudioRate.link_filtered(self.audioDecTee,
		#                      Gst.caps_from_string('audio/x-raw, format=S16LE, rate=48000')
		#)

		self.inputAudioRate.link_filtered(self.audioDecTee,
			Gst.caps_from_string('audio/x-raw')
		)


		# audio tools
		self.audioDecTee.link(self.audioEncoder)

		self.audioEncoder.link_filtered(self.encAudioTee,
			Gst.caps_from_string('audio/mpeg, stream-format=adts')
		)
		#self.audioEncoder.link_filtered(self.encAudioTee,
		#                      Gst.caps_from_string('audio/mpeg, stream-format=raw')
		#)

		# audio for quality 2
		#	self.hole.link_filtered(self.holeAudioEncoder, Gst.caps_from_string('audio/x-raw, channels=1, rate=8000'))
		#	self.holeAudioEncoder.link_filtered(self.outPut2.sink,
		#                                 Gst.caps_from_string('audio/mpeg, stream-format=adts'))
		#        self.encAudioTee.link(self.audioQueue2)
		#        self.audioQueue2.link(self.outPut2.sink)

		#	# audio 2
		#        self.encAudioTee.link(self.audioQueue2)
		#        self.audioQueue2.link(self.outPut2.sink)

		# audio for quality 0,1
		if(encoders[1]):
			self.encAudioTee.link(self.audioQueue1)
			self.audioQueue1.link(self.outPut1.sink)

		if(encoders[0]):
			self.encAudioTee.link(self.audioQueue0)
			#print "linking audiotee to sink"
			self.audio_sink_pad = Gst.Element.get_request_pad(self.outPut0.sink, 'audio_%u')
			#print "audio_sink_pad: ", self.audio_sink_pad, " || Type::: " , type(self.audio_sink_pad)
			self.audio_src_pad = self.audioQueue0.get_static_pad("src")
			#print "audio_src_pad: ", self.audio_src_pad, " || Type::: " , type(self.audio_src_pad)
			#print Gst.Pad.link(self.audio_src_pad, self.audio_sink_pad);
			#print 
			Gst.Pad.link_maybe_ghosting(self.audio_src_pad, self.audio_sink_pad);
			print("linking audiotee to sink DONE")

		print(self)
		Gst.debug_bin_to_dot_file( self.pipeline, Gst.DebugGraphDetails.ALL, 'pipeline')


	def formatdestination(self, selectedChannelId, qualityPrefix):
		return '/media/live/' + str(selectedChannelId) + '/' + qualityPrefix + '/' + '%010d.ts'

	def formatdestinationdir(self, selectedChannelId, qualityPrefix):
		return '/media/live/' + str(selectedChannelId) + '/' + qualityPrefix + '/'

	def getchannelinfo(self, channelVdrId):
		startFileIndex = 0
		currentChannel = Channel.select(Channel.q.vdrId == channelVdrId)
		self.currentChannel = (list(currentChannel))[0]
		print(self.currentChannel)

		#get last file index from db
		res = FileSegment.select( "file_segment.id = (SELECT MAX( file_segment.id ) FROM file_segment WHERE channel_id=%s )" % self.currentChannel.id)
		result = list(res)

		if (len(result) > 0):
			print('last file in table:', result[0].fileId)
			startFileIndex = result[0].fileId + 1

		print(startFileIndex)

		return (startFileIndex, self.currentChannel.id)

	# def on_sync_message(self, bus, msg):
	#     if (msg.src == self.outPut2.sink ):
	#         print('on_sync_message:', msg, msg.type, msg.src)

	def validate(self, time, outIndex):
		return
		global invalidTests

		timeStamps[outIndex] = time

		#	if(timeStamps[0]==timeStamps[1] and timeStamps[1]==timeStamps[2]):
		if(timeStamps[0]==timeStamps[1]):
			invalidTests=0
		else:
			invalidTests=invalidTests+1

		if(invalidTests > 10):
			print("invalidTests", invalidTests)
			print("!!!!!!!!!going to stop SEGMENT VALIDATE failed!!!!!!", time)
			self.kill()

	def on_message(self, bus, msg):
		elementNameMessage = ""
		if (msg.type == Gst.MessageType.ELEMENT):
			elementNameMessage = Gst.Element.get_name(msg.src)

		if (elementNameMessage == "splitmuxsink0"):
			st = msg.get_structure();
			if(st.get_name() == "splitmuxsink-fragment-closed"):
				print(str(datetime.now()))
				segmentRunningTime = st.get_value("running-time")
				startSegmentIndex = self.outPut0.startFileId
				currentSegmentIndex = self.outPut0.fileId
				self.outPut0.fileId = self.outPut0.fileId + 1
				segmentStartTime = appStartTime + ( (currentSegmentIndex - startSegmentIndex ) * defaultSegmentDuration / 1000000 )
				#self.validate(segmentStartTime, int(Gst.Element.get_name(msg.src)[-1]))
				print("[" + str(currentSegmentIndex) + "]time:" + str(time.gmtime(segmentStartTime / 1000)) + " segmentStartTime:" + str(segmentStartTime))
				print(elementNameMessage + " running-time: " + str(segmentRunningTime))
				self.validate( segmentRunningTime, int(Gst.Element.get_name(msg.src)[-1]))
				newFile = FileSegment(fileId=currentSegmentIndex, startTime=segmentStartTime, channel=self.currentChannel, duration=(defaultSegmentDuration / 1000000))
		elif (elementNameMessage == "splitmuxsink1"):
			st = msg.get_structure();
			if(st.get_name() == "splitmuxsink-fragment-closed"):
				segmentRunningTime = st.get_value("running-time")
				print(elementNameMessage + " running-time: " + str(segmentRunningTime))
				self.validate( segmentRunningTime, int(Gst.Element.get_name(msg.src)[-1]))
		elif (elementNameMessage == "splitmuxsink2"):
			st = msg.get_structure();
			if(st.get_name() == "splitmuxsink-fragment-closed"):
				segmentRunningTime = st.get_value("running-time")
				print(elementNameMessage + " running-time: " + str(segmentRunningTime))
				self.validate( segmentRunningTime, int(Gst.Element.get_name(msg.src)[-1]))
		elif (elementNameMessage == "multifilesink2"):
			st = msg.get_structure();
			print("running-time", st.get_value("running-time"))
			print("stream-time", st.get_value("stream-time"))
			
			currentSegmentEndTime = st.get_value("stream-time")
			#self.validate(currentSegmentEndTime)
			self.validate(currentSegmentEndTime, int(Gst.Element.get_name(msg.src)[-1]))
		elif (elementNameMessage == "multifilesink1"):
			st = msg.get_structure();
			print("running-time", st.get_value("running-time"))
			print("stream-time", st.get_value("stream-time"))
			
			currentSegmentEndTime = st.get_value("stream-time")
			self.validate(currentSegmentEndTime, int(Gst.Element.get_name(msg.src)[-1]))
		elif (elementNameMessage == "multifilesink0"):
			#             print('on_message:' , msg, msg.type, msg.src)
			#             if (msg.type == Gst.MessageType.TAG):
			#                 print(Gst.TagList.n_tags(msg.parse_tag()))
			#                 tagList = msg.parse_tag()
			#                 count = Gst.TagList.n_tags(tagList)
			#                 for i in range(count) :
			#                     print Gst.TagList.nth_tag_name(tagList, i)
			#             el
			# if (msg.type == Gst.MessageType.ELEMENT):
			#                print('on_message:' , msg, msg.type, msg.src)
			st = msg.get_structure();
			#                print(st.get_name())
			count = st.n_fields()
			#                 print(count)
			#                print("filename", st.get_value("filename"))
			#                print("index", st.get_value("index"))
			print("running-time", st.get_value("running-time"))
			print("stream-time", st.get_value("stream-time"))
			#                print("duration", st.get_value("duration"))
			#                print("segment-duration", st.get_value("segment-duration"))

			currentSegmentIndex = st.get_value("index")
			currentSegmentIndex = currentSegmentIndex - 1
			#currentSegmentEndTime = st.get_value("stream-time")
			currentSegmentEndTime = st.get_value("running-time")

			print("currentSegmentIndex", currentSegmentIndex)
			#self.validate(currentSegmentEndTime)
			self.validate(currentSegmentEndTime, int(Gst.Element.get_name(msg.src)[-1]))

			global lastSegmentEndTime

			# in ms
			segmentDuration = (currentSegmentEndTime - lastSegmentEndTime) / 1000000

			# in ms
			segmentStartTime = appStartTime + (lastSegmentEndTime / 1000000)

			print("[" + str(currentSegmentIndex) + "]time:" + str(time.gmtime(segmentStartTime / 1000)) + " segmentStartTime:" + str(segmentStartTime))

			newFile = FileSegment(fileId=currentSegmentIndex, startTime=segmentStartTime, channel=self.currentChannel, duration=segmentDuration)

			lastSegmentEndTime = currentSegmentEndTime

	def run(self):
		self.pipeline.set_state(Gst.State.PLAYING)
		self.mainloop.run()

	def kill(self):
		self.pipeline.set_state(Gst.State.NULL)
		self.mainloop.quit()

	def on_pad_added(self, element, pad):
		string = pad.query_caps(None).to_string()
		print('on_pad_added():', string)
		# print('on_pad_added():', str(pad))
		print(self.pipeline)
		#Gst.debug_bin_to_dot_file( self.pipeline, Gst.DebugGraphDetails.ALL, "error.dot")
		# if string.startswith(
		#         'audio/x-raw, format=(string)S16LE, layout=(string)interleaved, rate=(int)48000, channels=(int)2'):
		#     if self.audioLinked == False:
		#         pad.link(self.audioDecTee.get_static_pad('sink'))
		#         #pad.link(self.audioEncoder.get_static_pad('sink'))
		#         self.audioLinked = True
		#     else:
		#         print('audio already linked')
		# elif string.startswith(
		#         'audio/x-raw, format=(string)S16LE, layout=(string)interleaved, rate=(int)48000, channels=(int)1'):
		#     if self.audioLinked == False:
		#         pad.link(self.audioDecTee.get_static_pad('sink'))
		#         # pad.link(self.audioEncoder.get_static_pad('sink'))
		#         self.audioLinked = True
		#     else:
		#         print('audio already linked')
		if string.startswith('audio/mpeg'):
			if self.audioLinked == False:
				pad.link(self.audioParseDecoder.get_static_pad('sink'))
				self.audioLinked = True
			else:
				print('audio already linked')
		elif string.startswith('video/'):
			pad.link(self.videoParseDecoder.get_static_pad('sink'))
			#pad.link(self.inputVideoRate.get_static_pad('sink'))
			#pad.link(self.videoDecTee.get_static_pad('sink'))
		Gst.debug_bin_to_dot_file( self.pipeline, Gst.DebugGraphDetails.ALL, "pad_added")

	def on_eos(self, bus, msg):
		print('on_eos():' , msg, msg.type, msg.src, Gst.Element.get_name(msg.src))
		self.kill()

	def on_error(self, bus, msg):
		#Gst.debug_bin_to_dot_file_with_ts( self.pipeline, Gst.DebugGraphDetails.ALL, "error.dot")
		print('on_error():', msg.parse_error())
		self.kill()
