"""航线修订已增强时的飞行准入；不执行任何飞控指令。"""
import unittest
from shared.communication.flight import validate_route
from shared.communication.envelope import ProtocolError


class RouteAdmissionTests(unittest.TestCase):
    def test_verified_enhanced_revision_does_not_require_downgrade(self):
        a, c, b = ({'lat': -35.363262, 'lon': 149.1652373},
                   {'lat': -35.359262, 'lon': 149.1652373},
                   {'lat': -35.355262, 'lon': 149.1652373})
        receipt = {'revision': 2, 'stage': 'risk', 'protection_profile': 'enhanced',
                   'identity_verified': True, 'new_session_business_verified': True, 'workloads_reconciled': True}
        mission = {'revision': 2, 'state': 'running', 'plan': {'phases': []}, 'visited': [], 'violations': [],
                   'receipts': [receipt], 'spec': {'route': [a, c, b], 'alarms': [],
                   'risks': [{'id': 'C', 'center': c, 'radius_m': 200}], 'resources': {'cruise_speed_mps': 10}}}
        sample = {'position': a, 'sampled_at': 1000}
        self.assertEqual(validate_route(mission, sample, 'enhanced', 1000), mission['spec'])
        for changed in ({'revision': 1}, {'identity_verified': False}, {'new_session_business_verified': False}, {'protection_profile': 'lightweight'}, {'workloads_reconciled': False}):
            mission['receipts'] = [{**receipt, **changed}]
            with self.assertRaisesRegex(ProtocolError, 'receipt_missing'):
                validate_route(mission, sample, 'enhanced', 1000)
        mission['receipts'] = [receipt]
        with self.assertRaisesRegex(ProtocolError, 'enhanced'):
            validate_route(mission, sample, 'lightweight', 1000)
