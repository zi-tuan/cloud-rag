"""LOCAL TEST ONLY: real composed Guard with a simulated Kubernetes orchestrator."""
import json
import os
from pathlib import Path
from jetson.edge_agent.catalog import ServiceCatalog
from jetson.edge_agent.mcp_guard import GuardTools
from uav_rag.mcp_guard import guard_class
from uav_rag.mcp import serve

class SimulatedOrchestrator:
    def __init__(self):
        root = Path(__file__).resolve().parents[1]
        document = json.loads((root/'jetson/edge_agent/config/service-catalog.json').read_text())
        for entry in document['services']:
            entry['registry_verified'] = True  # Test fixture only, never persisted as deployment registration.
        self.catalog = ServiceCatalog(document)

    def activate(self, service_id):
        self.catalog.get(service_id, for_activation=True)
        return {'service_id': service_id, 'ready': True, 'simulated': True}

if __name__ == '__main__':
    serve(guard_class(GuardTools)(SimulatedOrchestrator(), os.environ['UAV_SELECTION_FILE']))
