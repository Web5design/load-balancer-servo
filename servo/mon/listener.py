# Copyright 2009-2013 Eucalyptus Systems, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see http://www.gnu.org/licenses/.
#
# Please contact Eucalyptus Systems, Inc., 6755 Hollister Ave., Goleta
# CA 93117, USA or visit http://www.eucalyptus.com/licenses/ if you need
# additional information or have any questions.

import socket
import sys
import os
import servo
import servo.config as config
from log import HttpLog
from log import TcpLog
import threading

class LogListener(threading.Thread):
    def __init__(self, stat):
        self.running = True
        self.stat = stat
        threading.Thread.__init__(self)

    def run(self):
        self.running = True
        server_address = config.CW_LISTENER_DOM_SOCKET
        try:
            os.unlink(server_address)
        except OSError:
            if os.path.exists(server_address):
                raise
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(server_address)
        self.socket = sock

        while self.running:
            data, client_address = sock.recvfrom(1024)
            try:
                log = self.parse(data)
                if log is not None:
                    self.stat.received(log)
                    print log
            except Exception, err:
                servo.log.warn('parsing failed: %s' % err)

    def parse(self, line):
        endmarker= line.rfind(':')
        if(endmarker > 0):
            line = line[endmarker+1:]
        else:
            raise Exception()
        line = line.strip()

        if line.startswith('httplog'):
            return HttpLog.parse(line)
        elif line.startswith('tcplog'):
            return TcpLog.parse(line)
 
    def stop(self):
        try:
            self.running = False
            self.socket.close()
        except:
            pass

