"""Pi → pi-mcp-adapter → 登记服务网关 → Kubernetes MCP；仅暴露目录操作。"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from shared.communication.envelope import ProtocolError, canonical
from jetson.edge_agent.catalog import GuardedOrchestrator, ServiceCatalog


class UpstreamMCP:
    def __init__(self, command):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1)
        self.messages = queue.Queue(maxsize=64)
        self.sequence = 0
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        result = self.call('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                          'clientInfo': {'name': 'uav-service-guard', 'version': '1'}})
        if result.get('serverInfo', {}).get('version') != 'v0.0.67':
            self.close()
            raise ProtocolError('Kubernetes MCP version differs from verified version')
        self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(2 * 1024 * 1024 + 1)
                if not line or len(line) > 2 * 1024 * 1024:
                    break
                self.messages.put(json.loads(line), timeout=1)
        except (ValueError, queue.Full, OSError):
            pass
        try:
            self.messages.put(None, timeout=1)
        except queue.Full:
            pass

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def call(self, method, params):
        import time
        with self.lock:
            self.sequence += 1
            identifier = self.sequence
            self._send({'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params})
            deadline = time.monotonic() + 30
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('MCP operation deadline exceeded; reconcile before retrying')
                try:
                    response = self.messages.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError('MCP response timed out; reconcile before retrying') from exc
                if response is None:
                    raise ProtocolError('MCP process exited')
                if response.get('id') == identifier:
                    if 'error' in response:
                        raise ProtocolError('MCP rejected request')
                    return response['result']

    def tool(self, name, arguments):
        return self.call('tools/call', {'name': name, 'arguments': arguments})

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


class GuardTools:
    def __init__(self, orchestrator, selection_file=None):
        self.orchestrator = orchestrator
        self.selection_file = Path(selection_file) if selection_file else None
        self.selected = None

    def tools(self):
        return [
            {'name': 'uav_protection_profiles', 'description': '读取登记防护等级与固定期限、配额；算法名称不代表防护等级。',
             'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
            {'name': 'uav_protection_plan', 'description': '提交航线任务的 transit/risk/exit 三阶段保护计划与选择理由，执行器依据新鲜遥测执行。',
             'inputSchema': {'type': 'object', 'properties': {'phases': {'type': 'array', 'items': {'type': 'object'}}, 'rationale': {'type': 'string'}},
                             'required': ['phases', 'rationale'], 'additionalProperties': False}},
            {'name': 'uav_authentication_policies', 'description': '读取登记身份认证策略，私钥和证明由独立执行器管理。',
             'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
            {'name': 'uav_combination_select', 'description': '记录唯一目标认证与加密组合，交由执行器激活、双向认证并验证；suite=null 表示保持当前加密。',
             'inputSchema': {'type': 'object', 'properties': {'auth_policy': {'type': 'string', 'enum': ['Ed25519', 'RSA', 'SM2']},
                            'suite': {'type': ['string', 'null'], 'enum': ['AES-256-GCM', 'SM4-GCM', None]},
                            'protection_profile': {'type': 'string', 'enum': ['lightweight', 'enhanced']}},
                             'required': ['auth_policy', 'suite', 'protection_profile'], 'additionalProperties': False}},
            {'name': 'uav_services', 'description': '读取登记服务、算法能力、固定镜像版本与 Harbor 验证状态。',
             'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
            *[{'name': name, 'description': description,
               'inputSchema': {'type': 'object', 'properties': {'service_id': {'type': 'string'}},
                               'required': ['service_id'], 'additionalProperties': False}}
              for name, description in [('uav_service_status', '通过 Kubernetes MCP 核对登记服务当前镜像、资源和 Ready 状态。'),
                                        ('uav_service_activate', '选择登记服务：不存在时通过 Kubernetes MCP 按登记模板创建配置、Service 和 Deployment；已存在时激活为一个副本。随后查询直到 Ready。')]]]

    def invoke(self, name, arguments):
        if not isinstance(arguments, dict):
            raise ProtocolError('invalid tool arguments')
        if name == 'uav_protection_profiles' and not arguments:
            from shared.communication.protection import PROFILES
            return {'profiles': PROFILES, 'trust_state_mutable': False, 'mandatory': ['mutual_identity', 'encryption', 'integrity', 'anti_replay']}
        if name == 'uav_protection_plan':
            from shared.communication.mission import validate_plan
            plan = validate_plan(arguments)
            for phase in plan['phases']:
                service = self.orchestrator.catalog.for_suite(phase['target']['suite'])
                self.orchestrator.catalog.get(service['id'], for_activation=True)
            if not self.selection_file:
                raise ProtocolError('mission receipt unavailable')
            receipt = {'selection_kind': 'mission_plan', 'plan': plan}
            if self.selection_file.exists() and json.loads(self.selection_file.read_text()) != receipt:
                raise ProtocolError('mission plan already selected')
            temporary = self.selection_file.with_suffix('.tmp')
            with temporary.open('w', encoding='utf-8') as out:
                out.write(canonical(receipt).decode())
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self.selection_file)
            return {'state': 'planned', 'requires_execution_verification': True, 'plan': plan}
        if name == 'uav_authentication_policies' and not arguments:
            return {'policies': ['Ed25519', 'RSA', 'SM2'], 'requires_registered_credentials': True}
        if name == 'uav_combination_select':
            from shared.communication.targets import validate_target
            target = validate_target(arguments)
            if not self.selection_file:
                raise ProtocolError('task selection receipt is unavailable')
            saved = json.loads(self.selection_file.read_text()) if self.selection_file.exists() else None
            if saved and saved.get('selection_kind') == 'combination':
                if saved['target'] != target:
                    raise ProtocolError('task already selected another combination')
                return {'target': target, 'state': 'selected', 'requires_execution_verification': True}
            service = self.orchestrator.catalog.for_suite(target['suite']) if target['suite'] else None
            previous = saved.get('service_id') if saved else self.selected
            if previous is not None and (service is None or previous != service['id']):
                raise ProtocolError('task already selected a target')
            if service is not None:
                self.orchestrator.activate(service['id'])
            temporary = self.selection_file.with_suffix('.tmp')
            with temporary.open('w', encoding='utf-8') as out:
                out.write(canonical({'selection_kind': 'combination', 'target': target}).decode())
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self.selection_file)
            self.selected = 'combination'
            return {'target': target, 'state': 'selected', 'requires_execution_verification': True}
        if name == 'uav_services' and not arguments:
            return self.orchestrator.catalog.public()
        if name not in ('uav_service_status', 'uav_service_activate') or set(arguments) != {'service_id'} or not isinstance(arguments['service_id'], str):
            raise ProtocolError('tool or arguments are not allowed')
        service_id = arguments['service_id']
        saved = json.loads(self.selection_file.read_text()) if self.selection_file and self.selection_file.exists() else None
        combination = saved is not None and saved.get('selection_kind') == 'combination'
        if name == 'uav_service_activate':
            if saved:
                selected_service = (self.orchestrator.catalog.for_suite(saved['target']['suite'])['id']
                                    if combination and saved['target']['suite'] else saved.get('service_id'))
                if selected_service != service_id:
                    raise ProtocolError('this task already selected another service; submit a new task to change selection')
            if not combination and self.selected is not None and self.selected != service_id:
                raise ProtocolError('only one service can be activated per task')
            result = self.orchestrator.activate(service_id)
            if not combination:
                self.selected = service_id
        else:
            result = self.orchestrator.status(service_id)
        if self.selection_file and not combination and service_id == self.selected and result['ready'] and result['registry_verified']:
            entry = self.orchestrator.catalog.get(service_id, for_activation=True)
            receipt = {**result, 'suite': entry['suite'], 'via': 'kubernetes-mcp-server-v0.0.67'}
            # 输出路径由执行器配置，不来自模型；只保存非敏感编排证据。
            temporary = self.selection_file.with_suffix('.tmp')
            with temporary.open('w', encoding='utf-8') as out:
                out.write(canonical(receipt).decode())
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self.selection_file)
        return result


def serve(guard):
    for line in sys.stdin:
        if len(line) > 1024 * 1024:
            return
        message = json.loads(line)
        if 'id' not in message:
            continue
        identifier = message['id']
        try:
            method = message['method']
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'uav-registered-services', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': guard.tools()}
            elif method == 'tools/call':
                params = message['params']
                result = {'content': [{'type': 'text', 'text': canonical(guard.invoke(params['name'], params.get('arguments', {}))).decode()}]}
            elif method == 'ping':
                result = {}
            else:
                raise ProtocolError('unsupported method')
            response = {'jsonrpc': '2.0', 'id': identifier, 'result': result}
        except (ProtocolError, OSError, ValueError, KeyError, TypeError):
            response = {'jsonrpc': '2.0', 'id': identifier, 'error': {'code': -32602, 'message': 'operation rejected or unavailable; reconcile service state'}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


def main():
    config = json.loads(Path(os.environ['UAV_MCP_GUARD_CONFIG']).read_text())
    upstream = UpstreamMCP(config['upstream_command'])
    try:
        guard = GuardTools(GuardedOrchestrator(ServiceCatalog.load(config['catalog']), upstream.tool,
                                               writes_enabled=config.get('writes_enabled') is True,
                                               templates=json.loads(Path(config['templates']).read_text()) if config.get('templates') else None),
                           os.environ.get('UAV_SELECTION_FILE'))
        serve(guard)
    finally:
        upstream.close()


if __name__ == '__main__':
    main()
