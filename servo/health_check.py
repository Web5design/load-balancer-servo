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
import threading
import time
import datetime
import httplib2
import socket
import ssl
import servo.ws
import servo.config as config
import servo.hostname_cache as hostname_cache
from servo.ws import StatefulInstance

health_check_config = None
 
class HealthCheckConfig(object):
    def __init__(self, interval=10, healthy_threshold=2, unhealthy_threshold=2, timeout=5, target='HTTP:80/'):
        self.interval=interval
        self.healthy_threshold=healthy_threshold
        self.unhealthy_threshold=unhealthy_threshold
        self.timeout=timeout
        self.target=target

    def __eq__(self, other):
        if not isinstance(other, HealthCheckConfig):
            return False
        if self.interval != other.interval:
            return False
        if self.healthy_threshold != other.healthy_threshold:
            return False
        if self.unhealthy_threshold != other.unhealthy_threshold:
            return False
        if self.timeout != other.timeout:
            return False
        if self.target != other.target:
            return False
        return True
    
    def __ne__(self, other):
        return not self.__eq__(other)
 
    def __repr__(self):
        return 'healthcheck-config: interval=%d, healthy_threshold=%d, unhealthy_threshold=%d, timeout=%d, target=%s' % (self.interval, self.healthy_threshold, self.unhealthy_threshold, self.timeout, self.target)
    
    def __str__(self):
        return self.__repr__()

class HealthCheckManager(object):
    def __init__(self):
        self.__check_map = dict()

    def set_instances(self, instances):
        if health_check_config is None:
            return
        new_instances=[]
        for instance_id in instances:
            if not self.__check_map.has_key(instance_id):
                new_instances.append(instance_id)
        del_instances=[]
        for instance_id in self.__check_map.keys():
            if not instance_id in instances:
                del_instances.append(instance_id)
        for instance_id in new_instances:
            self.register_instance(instance_id)
        for instance_id in del_instances:
            self.deregister_instance(instance_id)
        
    def register_instance(self, instance_id):
        if not self.__check_map.has_key(instance_id):
            self.__check_map[instance_id] = InstanceHealthChecker(instance_id)
            servo.log.info('starting to check %s' % instance_id)
            self.__check_map[instance_id].start()
 
    def has_instance(self, instance_id):
        return self.__check_map.has_key(instance_id)
    
    def deregister_instance(self, instance_id):
        if self.__check_map.has_key(instance_id):
            self.__check_map[instance_id].stop()
            del self.__check_map[instance_id]
            servo.log.info('stop checking %s' % instance_id)
 
    def reset(self):
        for instance_id in self.__check_map.keys():
            self.__check_map[instance_id].stop()
            del self.__check_map[instance_id]        
            servo.log.info('stop checking %s' % instance_id)

class InstanceHealthChecker(threading.Thread):
    def __init__(self, instance_id):
        self.instance_id = instance_id
        self.running = True
        threading.Thread.__init__(self)

    def run(self):
        self.ip_addr = hostname_cache.get_hostname(self.instance_id)
        if self.ip_addr is None:
            servo.log.error('could not find the ipaddress of the instance %s' % self.instance_id)
            return
        if health_check_config is None:
            servo.log.error('health check config is not set')
            return
        elb_host = config.get_clc_host()
        aws_access_key_id = config.get_access_key_id()
        aws_secret_access_key = config.get_secret_access_key()
        servo_instance_id = config.get_servo_id()

        con = servo.ws.connect_elb(host_name=elb_host, aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key)
        healthy = None 
        healthy_count = 0
        unhealthy_count = 0
        while (self.running):
            target = health_check_config.target
            result = None
            if target.upper().startswith('HTTPS'):
                result = self.check_https(target)
            elif target.upper().startswith('TCP'):
                result = self.check_tcp(target)
            elif target.upper().startswith('HTTP'):
                result = self.check_http(target)
            elif target.upper().startswith('SSL'):
                result = self.check_ssl(target)
            else:
                servo.log.error('unknown target: %s' % target)
            #print 'healthy? : %s, healthy_count: %d, unhealthy_count: %d, result: %s' % (healthy, healthy_count, unhealthy_count, result)
            if result is None:
                pass
            elif result:
                healthy_count += 1
                unhealthy_count = 0
                if (healthy is None or not healthy) and healthy_count >= health_check_config.healthy_threshold:
                    healthy = True
                    healthy_count = 0
                    instance = StatefulInstance(self.instance_id, 'InService')
                    servo.log.info('%s became InService' % self.instance_id)
                    try:
                        con.put_instance_health(servo_instance_id, [instance])
                    except Exception, err:
                        servo.log.error('failed to post servo states: %s' % err)
            else:
                unhealthy_count += 1
                healthy_count = 0
                if (healthy is None or healthy) and unhealthy_count >= health_check_config.unhealthy_threshold:
                    healthy = False
                    unhealthy_count = 0
                    instance = StatefulInstance(self.instance_id, 'OutOfService')
                    servo.log.info('%s became OutOfService' % self.instance_id)
                    try:
                        con.put_instance_health(servo_instance_id, [instance])
                    except Exception, err:
                        servo.log.error('failed to post servo states: %s' % err)

            if healthy_count > health_check_config.healthy_threshold:
                healthy_count = 0
            if unhealthy_count > health_check_config.unhealthy_threshold:
                unhealthy_count = 0
            start_time = time.time()
            while time.time() - start_time < health_check_config.interval and self.running:
                time.sleep(1)
           
    def check_http(self, target):
        target = target.replace('HTTP','').replace('http','').replace('Http','').replace(':','')
        idx = target.find('/')
        if idx < 0:
            return False
        port = target[:idx]
        path = target[idx:]
        url = "http://%s:%d%s" % (self.ip_addr, int(port), path)
        try:
            resp, content = httplib2.Http(timeout=health_check_config.timeout).request(url)
            if resp['status'] != '200':
                return False
            return True
        except socket.timeout: # probably timeout error
            return False
        except Exception, err:
            #servo.log.warn('unknown socket error to %s-%s' % (url, err)
            return False
    
    def check_tcp(self, target):
        idx = target.find(':')
        if idx < 0:
            return False
        port = target[idx+1:]
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(health_check_config.timeout)  
            s.connect((self.ip_addr, int(port)))
            return True
        except Exception, err:
            #servo.log.warn('tcp failed: (%s:%d) - %s' % (self.ip_addr, int(port), err))
            return False
        finally:
            if s is not None:
                s.close()
    
    def check_https(self, target):
        target = target.replace('HTTPS','').replace('https','').replace('Https','').replace(':','')
        idx = target.find('/')
        if idx < 0:
            return False
        port = target[:idx]
        path = target[idx:]
        url = "https://%s:%d%s" % (self.ip_addr, int(port), path)
        try:
            resp, content = httplib2.Https(timeout=health_check_config.timeout).request(url)
            if resp['status'] != '200':
                return False
            return True
        except socket.timeout: # probably timeout error
            return False
        except Exception, err:
            #servo.log.warn('unknown socket error to %s-%s' % (url, err)
            return False
        return True
 
    def check_ssl(self, target):
        idx = target.find(':')
        if idx < 0:
            return False
        port = target[idx+1:]
        ssl_sock = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(health_check_config.timeout)  
            ssl_sock = ssl.wrap_socket(s)
            ssl_sock.connect((self.ip_addr, int(port)))
            return True
        except Exception, err:
        #    servo.log.warn('ssl failed: (%s:%d) - %s' % (self.ip_addr, int(port), err))
            return False
        finally:
            if ssl_sock is not None:
                ssl_sock.close()
        return True
        
    def stop(self):
        self.running = False
