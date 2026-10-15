import unittest
from deploy.scripts.render_rag import render

class RagDeploymentTests(unittest.TestCase):
    def test_requires_versioned_inputs_and_keeps_database_readonly(self):
        image = 'registry.example/uav-rag@sha256:' + 'a'*64
        result = render('test-uav', image, 'test-knowledge', 'b'*16)
        deployment, service = result['items']
        pod = deployment['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        mount = pod['containers'][0]['volumeMounts'][0]
        self.assertTrue(mount['readOnly'])
        self.assertEqual(mount['subPath'], 'b'*16)
        self.assertEqual(service['spec']['type'], 'ClusterIP')
        self.assertEqual(deployment['spec']['strategy']['type'], 'Recreate')
        with self.assertRaises(ValueError):
            render('test-uav', 'registry.example/uav-rag:latest', 'test-knowledge', 'b'*16)
        with self.assertRaises(ValueError):
            render('test-uav', image, 'test-knowledge', '../escape')
