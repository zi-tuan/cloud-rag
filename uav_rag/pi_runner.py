"""Optional entrypoint overlay for the existing jetson.pi_runner.app module.

Requires the original UAV project on PYTHONPATH. The final MCP selection and
all activation/transaction behavior remain in its existing PiRunner.
"""
import json
import os
from pathlib import Path
import subprocess

from .client import RagClient
from .workflow import retrieve_for_task, augmented_scenario


def runner_class(base):
    class RagPiRunner(base):
        def _candidate(self, prompt, task_id):
            env = {k:v for k,v in os.environ.items() if k in
                   ('PATH','HOME','LANG','LC_ALL','SYSTEMROOT','WINDIR')}
            env.update(self.config.get('environment', {}))
            # The initial Pi call must have neither built-in nor extension tools.
            command = self.config.get('rag_candidate_command', self.config['command'])
            command = [*command, '--no-tools']
            options = {'start_new_session': True} if os.name == 'posix' else {'creationflags': subprocess.CREATE_NO_WINDOW}
            process = self.process_factory(command, cwd=self.config['working_directory'], env=env,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                text=True,encoding='utf-8',**options)
            with self.lock:
                self.processes[task_id] = process
                stopping = self.closed
            try:
                if stopping:
                    raise RuntimeError('runner stopping')
                output, _ = process.communicate(prompt,timeout=self.config.get('timeout_seconds',180))
                if process.returncode != 0 or len(output) > 65536:
                    raise ValueError('candidate planning failed')
                return json.loads(output)
            finally:
                self.stop_process(process)
                with self.lock:
                    self.processes.pop(task_id,None)

        def _run(self, task, digest):
            try:
                token_path = os.environ.get('UAV_RAG_TOKEN_FILE')
                token = Path(token_path).read_text().strip() if token_path else None
                client = RagClient(os.environ.get('UAV_RAG_URL','http://127.0.0.1:8787'),token)
                context = retrieve_for_task(task,lambda prompt:self._candidate(prompt,task['task_id']),client)
                # Keep prompts within the original 16k scenario boundary. Full excerpts
                # remain available from the RAG API; the model receives exact prefixes.
                compact = dict(context, services=[])
                for service in context['services']:
                    selected = dict(service,citations=[])
                    for citation in service['citations'][:1]:
                        lines = citation['text'].splitlines()
                        kept = []
                        for line in lines:
                            if sum(len(x)+1 for x in kept)+len(line) > 400:
                                break
                            kept.append(line)
                        if kept:
                            selected['citations'].append({**citation,'text':'\n'.join(kept),
                                'end_line':citation['start_line']+len(kept)-1})
                    compact['services'].append(selected)
                scenario = augmented_scenario(task['scenario'],compact)
                if len(scenario) > 16000:
                    raise ValueError('RAG augmented scenario exceeds runner limit')
                enriched = {**task,'scenario':scenario}
                # Original implementation executes final planning and writes MCP receipts.
                return super()._run(enriched,digest)
            except Exception:
                self.journal.save(task['task_id'],dict(state='failed',reason='rag_planning_or_retrieval_failed',task_digest=digest))
    return RagPiRunner


def main():
    from jetson.pi_runner import app
    app.PiRunner = runner_class(app.PiRunner)
    app.main()


if __name__ == '__main__':
    main()
