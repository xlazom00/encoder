#!/usr/bin/python3

# Live repacker: VDR channel (MPEG-TS over HTTP) -> 10 s MPEG-TS segments, nothing is re-encoded.
#
# Video (HEVC) and audio (AAC-LATM on DVB-T2) are copied by ffmpeg's segment muxer. GStreamer's
# mpegtsmux cannot take AAC-LATM (LOAS) without decoding it, ffmpeg's mpegts muxer can.
# Players need HEVC and AAC-LATM support (e.g. hls.js with LATM support).
#
# Audio: the first track stays muxed with the video (100/, as before); every further track gets its
# own audio-only segments (a1/, a2/ ...) with the same numbering, for HLS alternate audio renditions.
# <channel>/tracks.json describes all tracks (codec, language, channels ...) for the playlist/player.
#
# Timestamps: the segments keep the PTS/DTS/PCR of the source (-copyts, mpegts_copyts). ffmpeg does not
# repair source timestamp jumps then, and its segment muxer counts the cut points from the first
# timestamp of the run, so ffmpeg is restarted after a jump, when it stops finishing segments and once
# a day (libavformat unwraps only one 33-bit PTS wrap, which comes every 26.5 h).

import time
import sqlobject
from sqlobject import *
import MySQLdb.converters
import sys, os
import math
import json
import subprocess
import threading

# in ms
appStartTime = int(round(time.time() * 1000))
defaultSegmentDuration = 10000000000 #10s segments
defaultMissingSegments = 2
# in s
stallTimeout = 45
maxRunTime = 24 * 3600

# ffmpeg/ffprobe binaries, can be overridden for tests
ffmpegBinary = os.environ.get('FFMPEG', 'ffmpeg')
ffprobeBinary = os.environ.get('FFPROBE', 'ffprobe')

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

class EncoderPipeline:
	"""Repacks a VDR channel into 10 s MPEG-TS segments and registers every finished segment in
	file_segment. The decoder/encoder/resolution/codec/fps arguments of the former transcoding
	pipeline are kept for the channel scripts and ignored.

	outputRoot and registerSegments exist for tests: a test run must not write into /media/live or
	the database while the production repacker of the same channel is running.
	copyTimestamps=False makes ffmpeg rebase the timestamps to 0 (and repair jumps) instead."""
	def __init__(self, sourceServer, channelVdrId, decoderThreads=None, encoderThreads=None, encoderSpeed=None,
			targetResolution=None, videoCodec=None, targetFPS=None, outputRoot='/media/live', registerSegments=True,
			allAudioTracks=True, copyTimestamps=True):
		self.uri = sourceServer + channelVdrId + '.ts'
		self.registerSegments = registerSegments
		self.copyTimestamps = copyTimestamps
		self.process = None

		(startfileindex, currentchannelid) = self.getchannelinfo(channelVdrId)
		self.startFileIndex = startfileindex
		# numbering continues here when ffmpeg is restarted
		self.nextFileIndex = startfileindex

		# destination location (quality 100 only, the lower qualities needed an encoder)
		self.channelDirectory = os.path.join(outputRoot, str(currentchannelid))
		self.destinationdirectory0 = self.formatdestinationdir(outputRoot, currentchannelid, '100')
		self.destination0 = self.destinationdirectory0 + '%010d.ts'
		os.makedirs(self.destinationdirectory0, exist_ok=True)

		# further audio tracks get their own segment directories (a1/, a2/ ...)
		self.audioTracks = self.probeAudioTracks() if allAudioTracks else []
		for track in self.audioTracks[1:]:
			os.makedirs(self.formatdestinationdir(outputRoot, currentchannelid, track['directory']), exist_ok=True)
		self.writeTrackInfo(currentchannelid)

	def formatdestinationdir(self, outputRoot, selectedChannelId, qualityPrefix):
		return os.path.join(outputRoot, str(selectedChannelId), qualityPrefix) + '/'

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

	def probeAudioTracks(self):
		"""Audio tracks of the channel in ffmpeg's 0:a:N order. The first one stays muxed with the video
		(directory 100), every further one is written to its own directory a<N>."""
		try:
			result = subprocess.run([ffprobeBinary, '-v', 'error', '-analyzeduration', '3000000', '-probesize', '5000000',
				'-show_entries', 'stream=codec_type,codec_name,channels,channel_layout:stream_tags=language'
				':stream_disposition=visual_impaired,hearing_impaired', '-of', 'json', self.uri],
				capture_output=True, text=True, timeout=30)
			streams = json.loads(result.stdout or '{}').get('streams', [])
		except (subprocess.TimeoutExpired, ValueError) as e:
			print('audio track probe failed, only the first audio track is kept:', e)
			return []
		tracks = []
		for stream in streams:
			if stream.get('codec_type') != 'audio':
				continue
			index = len(tracks)
			language = stream.get('tags', {}).get('language', 'und')
			disposition = stream.get('disposition', {})
			tracks.append({
				'name': 'a%d' % index,
				'index': index,
				'directory': '100' if index == 0 else 'a%d' % index,
				'codec': stream.get('codec_name'),
				'language': language if len(language) == 3 and language.isalpha() else 'und',
				'channels': stream.get('channels'),
				'layout': stream.get('channel_layout'),
				'visualImpaired': bool(disposition.get('visual_impaired')),
				'hearingImpaired': bool(disposition.get('hearing_impaired')),
			})
		print('audio tracks:', ', '.join('%s %s/%s' % (t['name'], t['codec'], t['language']) for t in tracks))
		return tracks

	def writeTrackInfo(self, channelId):
		info = {'channel': channelId, 'tracks': self.audioTracks}
		tmp = os.path.join(self.channelDirectory, 'tracks.json.tmp')
		with open(tmp, 'w') as f:
			json.dump(info, f, indent=1)
		os.replace(tmp, os.path.join(self.channelDirectory, 'tracks.json'))

	def ffmpegArguments(self):
		segmentOptions = ['-c', 'copy', '-f', 'segment', '-segment_format', 'mpegts',
			'-segment_time', str(defaultSegmentDuration / 1000000000),
			'-segment_start_number', str(self.nextFileIndex)]
		if self.copyTimestamps:
			# without it the mpegts muxer shifts all timestamps by its mux delay (1.4 s)
			segmentOptions += ['-segment_format_options', 'mpegts_copyts=1']
		arguments = [ffmpegBinary, '-hide_banner', '-nostdin', '-nostats', '-loglevel', 'warning',
			'-fflags', '+genpts+discardcorrupt', '-err_detect', 'ignore_err',
			'-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_on_network_error', '1',
			'-reconnect_delay_max', '5', '-rw_timeout', '15000000'] + (
			['-copyts'] if self.copyTimestamps else []) + [
			'-i', self.uri,
			# first video and first audio track as before; '?' keeps channels without one working
			'-map', '0:v:0?', '-map', '0:a:0?'] + segmentOptions + [
			# cut at the first keyframe after every 10 s; each finished segment is reported on stdout
			# as "file,start,end" (stream time in seconds)
			'-segment_list', 'pipe:1', '-segment_list_type', 'csv',
			self.destination0]
		# further audio tracks: audio-only segments cut at every 10 s, numbered like the video segments
		for track in self.audioTracks[1:]:
			arguments += ['-map', '0:a:%d' % track['index']] + segmentOptions + [
				os.path.join(self.channelDirectory, track['directory'], '%010d.ts')]
		return arguments

	def run(self):
		while True:
			arguments = self.ffmpegArguments()
			print(' '.join(arguments))
			sys.stdout.flush()
			self.restart = None
			self.segmentsInRun = 0
			self.runStarted = self.lastSegmentClosed = time.monotonic()
			self.process = subprocess.Popen(arguments, stdout=subprocess.PIPE, text=True)
			threading.Thread(target=self.watchdog, args=(self.process,), daemon=True).start()
			for entry in self.process.stdout:
				self.on_segment_closed(entry.strip())
				if time.monotonic() - self.runStarted > maxRunTime:
					self.requestRestart('daily restart')
			returnCode = self.process.wait()
			# a run without any segment is left to the channel script's loop (it waits 60 s)
			if self.restart is None or self.segmentsInRun == 0:
				return returnCode
			print('ffmpeg restarts (%s) with segment %d' % (self.restart, self.nextFileIndex))

	def kill(self):
		if self.process and self.process.poll() is None:
			self.process.terminate()

	def requestRestart(self, reason):
		if self.restart is None:
			self.restart = reason
			self.restartRequested = time.monotonic()
			self.kill()

	def watchdog(self, process):
		"""Restarts ffmpeg when it stops finishing segments (no data, or source timestamps that jumped
		back, so the segment muxer does not cut any more) and kills it when it ignores SIGTERM."""
		while process.poll() is None:
			time.sleep(1)
			if self.restart is None and time.monotonic() - self.lastSegmentClosed > stallTimeout:
				self.requestRestart('no segment for %d s' % stallTimeout)
			elif self.restart is not None and time.monotonic() - self.restartRequested > 15:
				process.kill()

	def firstSegmentStart(self, fileName, end):
		"""Start of the first segment of a run, the segment list reports 0 for it. With copied timestamps
		it is the first timestamp of the reference stream (video, else audio) in the file."""
		try:
			result = subprocess.run([ffprobeBinary, '-v', 'error', '-show_entries', 'stream=codec_type,start_time',
				'-of', 'json', fileName], capture_output=True, text=True, timeout=10)
			starts = {}
			for stream in json.loads(result.stdout or '{}').get('streams', []):
				if stream.get('start_time') not in (None, 'N/A'):
					starts.setdefault(stream.get('codec_type'), float(stream['start_time']))
			start = starts.get('video', starts.get('audio'))
			if start is not None and 0 < end - start <= 3 * defaultSegmentDuration / 1000000000:
				return start
			print('no usable start time in', fileName, starts)
		except (subprocess.TimeoutExpired, ValueError) as e:
			print('cannot read the start time of', fileName, e)
		return end - defaultSegmentDuration / 1000000000

	def on_segment_closed(self, entry):
		try:
			fileName, start, end = entry.rsplit(',', 2)
			fileId = int(os.path.splitext(os.path.basename(fileName))[0])
			start = float(start)
			end = float(end)
		except ValueError:
			print('unexpected segment list entry:', entry)
			return

		now = time.time()
		maxDuration = 3 * defaultSegmentDuration / 1000000000
		if self.segmentsInRun == 0:
			if self.copyTimestamps:
				start = self.firstSegmentStart(os.path.join(self.destinationdirectory0, os.path.basename(fileName)), end)
			# stream time -> wall clock: the first segment of a run has just ended
			self.clockOffset = end - now
		elif not (0 < end - start <= maxDuration and abs(start - self.lastEnd) < 2):
			# the source timestamps jumped: continue the database times from the previous segment,
			# restart ffmpeg so that its segment muxer cuts every 10 s again
			print('timestamp jump: previous segment ended at %.3f, this one is %.3f - %.3f' % (self.lastEnd, start, end))
			if not 0 < end - start <= maxDuration:
				start = end - max(0, now - self.lastSegmentWallEnd)
			self.clockOffset = start - self.lastSegmentWallEnd
			self.requestRestart('timestamp jump')
		segmentStartTime = int(round((start - self.clockOffset) * 1000))
		segmentDuration = int(round((end - start) * 1000))
		self.lastEnd = end
		self.lastSegmentWallEnd = end - self.clockOffset
		self.segmentsInRun += 1
		self.nextFileIndex = fileId + 1
		self.lastSegmentClosed = time.monotonic()
		print("[" + str(fileId) + "]time:" + str(time.gmtime(segmentStartTime / 1000)) +
			" segmentStartTime:" + str(segmentStartTime) + " duration:" + str(segmentDuration))
		sys.stdout.flush()
		if self.registerSegments:
			FileSegment(fileId=fileId, startTime=segmentStartTime, channel=self.currentChannel, duration=segmentDuration)
