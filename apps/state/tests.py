import gevent
import requests

from django.test import LiveServerTestCase, TestCase
from django.utils import timezone

from nodes.models import Node
from users.models import Group

from .helpers import (extract_disk_available, extract_node_software_version,
    sizeof_fmt)
from .models import State, StateHistory
from .notifications import NodeNotAvailable
from .settings import (STATE_NODE_OFFLINE_WARNING, STATE_NODE_SOFT_VERSION_URL,
    STATE_NODE_SOFT_VERSION_NAME)

class StateTests(LiveServerTestCase):
    def setUp(self):
        group = Group.objects.create(name='Group', allow_nodes=True)
        self.node = Node.objects.create(name='Test', group=group)
    
    def test_store_glet(self):
        """Tests State.store_glet (#468)"""
        glet = gevent.spawn(requests.get, self.live_server_url, headers={})
        gevent.joinall([glet])
        # Should work without raising TypeError exception (#468)
        # CaseInsensitiveDict is not JSON serializable
        try:
            State.store_glet(self.node, glet)
        except TypeError as e:
            self.fail('State.store_glet() raised TypeError: %s' % e)
    
    def test_helper_sizeof_fmt(self):
        # Test helper used by disk_available (#339)
        self.assertIn('MiB', sizeof_fmt(0))
        self.assertIn('GiB', sizeof_fmt(-1 * 2**10))
        self.assertIn('GiB', sizeof_fmt(2**10))
        self.assertIn('TiB', sizeof_fmt(2**20))
        self.assertIn('TiB', sizeof_fmt(2**50))
        self.assertEquals('N/A', sizeof_fmt('N/A'))
    
    def test_node_soft_version(self):
        # master-trunk.f1a07e0-r20140213.0946-1
        NODE_SCHEMA = "%(branch)s.%(rev)s-%(date)s-%(pkg)s"
        NODE_VERSIONS = [
            # branch with a dot '.'
            {'branch': 'master.trunk' , 'rev': 'f1a07e0', 'date': 'r20140213.0946', 'pkg': '1'},
            # branch with a dash '-'
            {'branch': 'master-trunk' , 'rev': '1130af0', 'date': 'r20130911.0946', 'pkg': '1'},
            # empty branch name
            {'branch': '' , 'rev': '1234567', 'date': 'r20131101.0826', 'pkg': '1'},
            # empty data
            {'branch': '' , 'rev': '', 'date': '', 'pkg': ''},
        ]
        for node_version in NODE_VERSIONS:
            version_raw = NODE_SCHEMA % node_version
            version = extract_node_software_version(version_raw)
            self.assertEquals(version, node_version)
            
            # Try to generate url and name based on settings
            STATE_NODE_SOFT_VERSION_URL(version)
            STATE_NODE_SOFT_VERSION_NAME(version)
    
    def test_disk_available(self):
        data = {
            "name": "UPC-lab104-VM07",
            "soft_version": "testing.bd6d896-r20140902.1052-1",
            "resources": [
                {
                    "avail": 5250,
                    "dflt_req": 200,
                    "max_req": 1000,
                    "name": "disk",
                    "unit": "MiB"
                },
                {
                    "avail": 8,
                    "dflt_req": 0,
                    "max_req": 1,
                    "name": "pub_ipv4",
                    "unit": "addrs"
                },
                {
                    "avail": 0,
                    "dflt_req": 0,
                    "max_req": 0,
                    "name": "pub_ipv6",
                    "unit": "addrs"
                }
            ]
        }
        disk = extract_disk_available(data)
        self.assertEqual(5250, disk['total'])
        self.assertEqual(200, disk['slv_dflt'])
        self.assertEqual("MiB", disk['unit'])
    
    def test_disk_available_old_firmware(self):
        data = {
            "name": "Gallia",
            "soft_version": "testing.70de099-r20140211.1450-1",
            "disk_avail": 40202,
            "disk_dflt_per_sliver": 1000,
            "disk_max_per_sliver": 2000
        }
        disk = extract_disk_available(data)
        self.assertEqual(40202, disk['total'])
        self.assertEqual(1000, disk['slv_dflt'])
        self.assertIsNone(disk['unit'])
    
    def test_disk_available_nodata(self):
        data = {
            "name": "Gallia",
            "soft_version": "testing.70de099-r20140211.1450-1"
        }
        disk = extract_disk_available(data)
        self.assertEqual('N/A', disk['total'])
        self.assertEqual('N/A', disk['slv_dflt'])
        self.assertIsNone(disk['unit'])


class NotAvailableNotificationTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name='Group', allow_nodes=True)
        self.node = Node.objects.create(name='Test', group=self.group)
        self.na = NodeNotAvailable()
    
    def test_node_offline_without_history(self):
        State.objects.create(content_object=self.node, value=State.OFFLINE)
        self.assertFalse(self.na.check_condition(self.group))
    
    def test_node_offline_long_time(self):
        state = State.objects.create(content_object=self.node, value=State.OFFLINE)
        end = timezone.now()
        start = end - 2*STATE_NODE_OFFLINE_WARNING
        StateHistory.objects.create(state=state, value=State.OFFLINE, start=start, end=end)
        self.assertTrue(self.na.check_condition(self.group))
    
    def test_node_flapping_state(self):
        state = State.objects.create(content_object=self.node, value=State.OFFLINE)
        # offline state history
        end = timezone.now()
        start = end - STATE_NODE_OFFLINE_WARNING/2
        StateHistory.objects.create(state=state, value=State.OFFLINE, start=start, end=end)
        
        # production state history
        end = start
        start = end - STATE_NODE_OFFLINE_WARNING/2
        StateHistory.objects.create(state=state, value=Node.PRODUCTION, start=start, end=end)
        
        self.assertFalse(self.na.check_condition(self.group))
