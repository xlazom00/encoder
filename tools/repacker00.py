#!/usr/bin/python3

import time
import sqlobject
from sqlobject import *
import MySQLdb.converters
import sys, os
from datetime import datetime

import gi

gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst
#, GstMpegts

GObject.threads_init()
Gst.init(None)

# in ms
encoderStartTime = int(round(time.time() * 1000))
defaultSegmentDuration = 10000000000 #10s segments

#lastSegmentEndTime = 0

timeStamps = [0,0,0]
invalidTests = 0


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


class OutPut(Gst.Bin):
	def __init__(self, startFileIndex, outputDirectory, index):
		Gst.Bin.__init__(self)
		self.startFileId = startFileIndex
		self.fileId = startFileIndex

		self.sink = Gst.ElementFactory.make('splitmuxsink', 'splitmuxsink' + index)

		self.add(self.sink)
		self.sink.set_property('start-index', startFileIndex)
		self.sink.set_property('location', outputDirectory)
		self.sink.set_property('max-files', 0)
		self.sink.set_property('max-size-time', defaultSegmentDuration) #10s segments
		self.sink.set_property('send-keyframe-requests', 'false')


class RepackerPipeline:
	def __init__(self, sourceServer, channelVdrId):
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
		self.player = Gst.ElementFactory.make('uridecodebin', None)
		caps = Gst.Caps.from_string("video/x-h265; audio/mpeg; audio/x-ac3; text/x-raw")
		self.player.set_property('caps', caps)

		self.player.set_property('uri', self.uri + ".ts")
		self.player.set_property('use-buffering', 'true')
		self.player.set_property('force-sw-decoders', 'true')
		self.player.set_property('buffer-size', 20000000)


		self.videoParse = Gst.ElementFactory.make('h265parse', None)
		self.pipeline.add(self.videoParser)

		self.audioParse = Gst.ElementFactory.make('aacparse', None)
		self.pipeline.add(self.audioParse)

		self.outPut2 = OutPut(self.startFileIndex, self.destination2, "2")
		self.pipeline.add(self.outPut2)


		self.audioLinked = False

		# Connect signal handlers
		self.player.connect('pad-added', self.on_pad_added)


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
		#res = FileSegment.select("file_segment.id = (SELECT MAX( file_segment.id ) FROM file_segment WHERE channel_id= )" % )
		res = FileSegment.select( "file_segment.id = (SELECT MAX( file_segment.id ) FROM file_segment WHERE channel_id=%s )" % self.currentChannel.id)
		#res = FileSegment.select("channel_id=%s" % 20 )
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
		#if (msg.type == Gst.MessageType.QOS):
		#print('on_message:' , msg, msg.type, msg.src, Gst.Element.get_name(msg.src))
		#if (msg.type == Gst.MessageType.EOS):
		#	print('on_message:' , msg, msg.type, msg.src, Gst.Element.get_name(msg.src))
#	pass
#	Gst.debug_bin_to_dot_file (self.pipeline, Gst.DebugGraphDetails.ALL, 'encodergraph.dot')
        #print('on_message:' , msg, msg.type, msg.src)
	#if (msg.type == Gst.MessageType.ELEMENT):
	#if (msg.type == Gst.MessageType.ELEMENT and (Gst.Element.get_name(msg.src) == "splitmuxsink0")):
	#    print('on_message:' , msg, msg.type, msg.src, Gst.Element.get_name(msg.src))
        # if (msg.type== Gst.MessageType.ELEMENT):
        #     print('on_message:' , msg, msg.type, msg.src, Gst.Element.get_name(msg.src))
        #    st = msg.get_structure()
            #print(st.get_name())

            # if(st.get_name().startswith('pmt')):
        #    print(st.to_string())
        #    count = st.n_fields()
        #    for i in range(count) :
        #	print (st.nth_field_name(i))
            #     section = st['section']
            #     print (str(section))
            #     print (section.section_type)
            #     pmt = section.get_pmt()
            #     print (pmt)
            #     streams = pmt.streams
            #     descriptors = pmt.descriptors
            #     print(len(streams))
            #     print(len(descriptors))
            #     for s in streams :
            #         print ("stream:"+ str(s.pid))
            #         print s.stream_type
            #         for d in s.descriptors :
            #             print d
            #             print d.tag
            #             print("languages:" + str(d.parse_iso_639_language_nb()))





                # print len(pat)
                # print (section.subtable_extension)
                # print (section.packetizer)
                # print (section.pid)
                # print (section.section_length)

                # print (section.get_cat())
                # pmt = section.get_pmt()
                # nit = section.get_nit()

                # print (nit.streams)

                # print (st['section'].pid)

            # if(st.has_field('pat')):
            #     print(st['pat'])
            #     print(st['section'])
            #     print(st.to_string())
            #     print(st.get_name())

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
				segmentStartTime = encoderStartTime + ( (currentSegmentIndex - startSegmentIndex ) * defaultSegmentDuration / 1000000 )
				#self.validate(segmentStartTime, int(Gst.Element.get_name(msg.src)[-1]))
				print("[" + str(currentSegmentIndex) + "]time:" + str(time.gmtime(segmentStartTime / 1000)) + " segmentStartTime:" + str(segmentStartTime))
				print(elementNameMessage + " running-time: " + str(segmentRunningTime))
				self.validate( segmentRunningTime, int(Gst.Element.get_name(msg.src)[-1]))
				newFile = FileSegment(fileId=currentSegmentIndex, startTime=segmentStartTime, channel=self.currentChannel, duration=(defaultSegmentDuration / 1000000))

		if (elementNameMessage == "splitmuxsink1"):
			st = msg.get_structure();
			if(st.get_name() == "splitmuxsink-fragment-closed"):
				segmentRunningTime = st.get_value("running-time")
				print(elementNameMessage + " running-time: " + str(segmentRunningTime))
				self.validate( segmentRunningTime, int(Gst.Element.get_name(msg.src)[-1]))

		if (elementNameMessage == "splitmuxsink2"):
			st = msg.get_structure();
			if(st.get_name() == "splitmuxsink-fragment-closed"):
				segmentRunningTime = st.get_value("running-time")
				print(elementNameMessage + " running-time: " + str(segmentRunningTime))
				self.validate( segmentRunningTime, int(Gst.Element.get_name(msg.src)[-1]))

		if (msg.type == Gst.MessageType.ELEMENT and (Gst.Element.get_name(msg.src) == "multifilesink2")):
			st = msg.get_structure();
			print("running-time", st.get_value("running-time"))
			print("stream-time", st.get_value("stream-time"))

			currentSegmentEndTime = st.get_value("stream-time")
			#self.validate(currentSegmentEndTime)
			self.validate(currentSegmentEndTime, int(Gst.Element.get_name(msg.src)[-1]))
			return

		if (msg.type == Gst.MessageType.ELEMENT and (Gst.Element.get_name(msg.src) == "multifilesink1")):
			st = msg.get_structure();
			print("running-time", st.get_value("running-time"))
			print("stream-time", st.get_value("stream-time"))

			currentSegmentEndTime = st.get_value("stream-time")
			self.validate(currentSegmentEndTime, int(Gst.Element.get_name(msg.src)[-1]))

			return

		if (msg.type == Gst.MessageType.ELEMENT and (Gst.Element.get_name(msg.src) == "multifilesink0")):
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
			segmentStartTime = encoderStartTime + (lastSegmentEndTime / 1000000)

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
