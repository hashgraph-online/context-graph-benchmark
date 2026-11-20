import copy
from pathlib import Path
import unittest

from cgb.adapters import Engine
from cgb.contracts import workload
from cgb.jsonio import Invalid, read_json
from cgb.metrics.evidence import covered_units
from cgb.render import verify_context
from cgb.tokenization import Counter

ROOT = Path(__file__).resolve().parents[1]


class Graph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.public = read_json(ROOT / 'datasets/synthetic/graph-behavior-workload.json')
        cls.labels = {r['task']: r for r in read_json(ROOT / 'datasets/synthetic/graph-behavior-labels.json')['tasks']}
        cls.counter = Counter(ROOT / '.cgb/tokenizer')

    def execute(self, task, public=None):
        public = public or self.public
        engine = Engine('bm25', self.counter)
        engine.reset('fixture')
        engine.handle('ingest', {'documents': public['documents'], 'edges': public['edges']})
        for event in task['events']:
            engine.handle(event['operation'], {k:v for k,v in event.items() if k != 'operation'})
        return engine.query({**task, 'namespace':'fixture', 'budget':4096})

    def test_every_family_positive_and_boundary_oracles(self):
        families = set()
        for task in self.public['tasks']:
            with self.subTest(task=task['id']):
                families.add(task['family'])
                oracle = self.labels[task['id']]
                status, payload, _ = self.execute(task)
                self.assertEqual(status, oracle['expected_status'])
                if status == 'ok':
                    observed = verify_context(payload['blocks'], self.public, task, 4096, self.counter)
                    self.assertFalse(observed['violations'])
                    self.assertEqual(len(covered_units(oracle['units'], observed['spans'])), len(oracle['units']))
                    # Deliberately omit all returned text: each positive oracle detects it.
                    self.assertLess(len(covered_units(oracle['units'], [])), len(oracle['units']))
                else:
                    # Empty successful responses cannot satisfy a refusal oracle.
                    self.assertNotEqual('ok', oracle['expected_status'])
        self.assertEqual(len(families), 7)

    def test_stale_version_and_revoked_scope(self):
        old = next(d for d in self.public['documents'] if d['id']=='changing')
        changed = next(t for t in self.public['tasks'] if t['id']=='source-changes-updated')
        self.assertIn('stale-source', verify_context([Engine.block(old)], self.public, changed, 512, self.counter)['violations'])
        revoked = next(t for t in self.public['tasks'] if t['id']=='policy-boundary-revoked')
        secret = next(d for d in self.public['documents'] if d['id']=='secret')
        self.assertIn('unauthorized-or-deleted-source', verify_context([Engine.block(secret)], self.public, revoked, 512, self.counter)['violations'])

    def test_cycle_and_deleted_dependency(self):
        public = copy.deepcopy(self.public)
        public['edges'].append({'from':'dependency','to':'action','kind':'requires'})
        task = next(t for t in public['tasks'] if t['id']=='relationship-closure-closure')
        status, payload, _ = self.execute(task, public)
        self.assertEqual(status,'ok')
        selected = [s['document_id'] for b in payload['blocks'] for s in b['citations']]
        self.assertEqual(selected.count('action'),1)
        task['events'] = [{'operation':'delete','documents':[],'ids':['dependency'],'scope':None}]
        self.assertEqual(self.execute(task, public)[0],'refused')

    def test_out_of_order_revision_uses_validity_not_arrival(self):
        task = copy.deepcopy(next(t for t in self.public['tasks'] if t['id']=='source-changes-updated'))
        new = task['events'][0]['documents'][0]
        old = next(d for d in self.public['documents'] if d['id']=='changing')
        task['events'].append({'operation':'update','documents':[old],'ids':[],'scope':None})
        status,payload,_ = self.execute(task)
        self.assertEqual(status,'ok')
        versions = {s['version'] for b in payload['blocks'] for s in b['citations'] if s['document_id']=='changing'}
        self.assertEqual(versions,{new['version']})

    def test_mutating_an_immutable_version_is_rejected(self):
        public = copy.deepcopy(self.public)
        task = public['tasks'][0]
        changed = {**public['documents'][0], 'text':'rewritten under same version'}
        task['events']=[{'operation':'update','documents':[changed],'ids':[],'scope':None}]
        with self.assertRaises(Invalid): workload(public)


if __name__ == '__main__':
    unittest.main()
