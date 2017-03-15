#!/usr/bin/python
"""
Convert dmesg logs to .rd file
dmesg of form
 1226.339537] @MF@ dumping IB gpu=8d3003b8 host=e53013b8 hdr='@MF@ ib=4 ic=0 >'
 1226.346722] @MF@ ib=4 ic=0 >00000000: c0032d00 00040000 000000e0 00000005 00038000 0000039c 00000040 c0012d00
[ 1226.356679] @MF@ ib=4 ic=0 >00000020: 00040001 00000205 c0022d00 0004000e 00000000 010000e0 c0012d00 00040001
...
[ 1226.515697] @MF@ dumping sub IB gpu=8d300180 host=e5301180
[ 1226.521204] @MF@ ib=4 sub=8d300180 >00000000: 0000057d 00000005 c0022d00 00040204 00000000 00090240 c0042d00 00040280
...

RD file of form
TLV..  where T,L 32 bit values

Where T is:
RD_GPU_ID
	205
RD_GPU_ADDR
	u32 gpuaddr
	u32 len
RD_BUFFER_CONTENTS
	data
RD_CMDSTREAM_ADDRESS
	u32 gpuaddr
	u32 len

Parsing is stateful so must appear in above order

So we create a RD_GPU_ADDR, RD_BUFFER_CONTENTS pair for each dump in the dmesg stream
And a RD_CMDSTREAM_ADDRESS for each toplevel submission (in increasing ib order)
"""

import re
import struct
import sys


class RdGenerator:
	RD_NONE = 0
	RD_TEST = 1       # ascii text */
	RD_CMD = 2        # ascii text */
	RD_GPUADDR = 3    # u32 gpuaddr, u32 size */
	RD_CONTEXT = 4    # raw dump */
	RD_CMDSTREAM = 5  # raw dump */
	RD_CMDSTREAM_ADDR = 6 # gpu addr of cmdstream */
	RD_PARAM = 7      # u32 param_type, u32 param_val, u32 bitlen */
	RD_FLUSH = 8     # empty, clear previous params */
	RD_PROGRAM = 9   # shader program, raw dump */
	RD_VERT_SHADER = 10
	RD_FRAG_SHADER = 11
	RD_BUFFER_CONTENTS = 12
	RD_GPU_ID = 13

	def __init__(self, out):
		self._out = out

	def writeGpuId(self, gpuId):
		self._out.write(struct.pack("<III", self.RD_GPU_ID, 4, 205))

	def writeBuffer(self, buf):
		dwords = len(buf.contents)
		self._out.write(struct.pack("<IIII", self.RD_GPUADDR, 8, buf.gpuaddr, dwords * 4))
		self._out.write(struct.pack("<II", self.RD_BUFFER_CONTENTS, dwords * 4))
		for i in xrange(dwords):
			self._out.write(struct.pack("<I", buf.contents[i]))

	def writeCmdStream(self, buf):
		dwords = len(buf.contents)
		self._out.write(struct.pack("<IIII", self.RD_CMDSTREAM_ADDR, 8, buf.gpuaddr, dwords))


class GpuBuffer:
	def __init__(self, gpuaddr, isMain):
		self.gpuaddr = gpuaddr
		self.isMain = isMain
		self.contents = []

	def append(self, value):
		self.contents.append(value)

        def __len__(self):
		return len(self.contents) * 4

# TODO detect premature ending of cmdbuf
class DmesgParser:
	PATTERN_MAIN_HDR = re.compile(r"@MF@ dumping IB gpu=(.*) host=.* ib=(.*) ic=")
	PATTERN_MAIN_DATA = re.compile(r"] @MF@ ib=.* ic=.* >(.*):(.*)")
	PATTERN_SUB_HDR = re.compile(r"@MF@ dumping sub IB gpu=(.*) host=")
	PATTERN_SUB_DATA = re.compile(r"] @MF@ ib=.* sub=.* >(.*):(.*)")


	def __init__(self):
		self._buffers = []
		self._curBuffer = None

	def getBuffers(self):
		return self._buffers

	def processFile(self, filename):
		for line in open(filename, "r"):
			line = line.strip()
			self._processLine(line)

		self._nextBuffer()

	def _processLine(self, line):
		for p, f in (
			(self.PATTERN_MAIN_HDR, self._processMainHdr),
			(self.PATTERN_MAIN_DATA, self._processData),
			(self.PATTERN_SUB_HDR, self._processSubHdr),
			(self.PATTERN_SUB_DATA, self._processData),
		):
			match = p.search(line)
			if match is not None:
				f(match)
				return

	def _nextBuffer(self):
		if self._curBuffer:
			self._buffers.append(self._curBuffer)
			self._curBuffer = None

	def _processMainHdr(self,match):
		self._nextBuffer()
		gpuaddr = int(match.group(1), 16)
		self._curBuffer = GpuBuffer(gpuaddr, True)

	def _processSubHdr(self,match):
		self._nextBuffer()
		gpuaddr = int(match.group(1), 16)
		self._curBuffer = GpuBuffer(gpuaddr, False)

	def _processData(self, match):
		if int(match.group(1), 16) != len(self._curBuffer):
			print("Offset mismatch %s versus %08x, line: %s" %
	      			(match.group(1), len(self._curBuffer), match.group(0)))
			print("Log was probably truncated!")
			exit(1)
		for hexword in match.group(2).split(" "):
			if not hexword:
				continue
			self._curBuffer.append(int(hexword, 16))


if __name__ == "__main__":
	parser = DmesgParser()
	parser.processFile(sys.argv[1])

	g = RdGenerator(open(sys.argv[2], "wb"))
	g.writeGpuId(205)

	for buf in parser.getBuffers():
		g.writeBuffer(buf)

	for buf in parser.getBuffers():
		if buf.isMain:
			g.writeCmdStream(buf)

