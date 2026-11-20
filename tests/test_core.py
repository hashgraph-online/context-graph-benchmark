import copy
import json
from pathlib import Path
import tempfile
import unittest

from cgb.contracts import StrictValidator, labels, validate, workload
from cgb.evidence import seal, verify
from cgb.jsonio import Invalid, decode, digest, encoded, read_json, write_json
from cgb.metrics.evidence import covered_units, reduce_rows
from cgb.render import verify_context
from cgb.schema_defs import REGISTRY
from cgb.statistics import paired_recall, wilson
from cgb.tokenization import Counter

ROOT = Path(__file__).resolve().parents[1]


class Contracts(unittest.TestCase):
    def test_schema_valid(self):
        StrictValidator.check_schema(REGISTRY)

    def test_strict_json(self):
        for payload in [b'{"a":1,"a":2}', b'{"a":NaN}', b'1e999', b'"\\ud800"', b'"\xff"', b'[' * 34 + b'0' + b']' * 34]:
            with self.subTest(payload=payload), self.assertRaises(Invalid):
                decode(payload)

    def test_canonical_bytes(self):
        self.assertEqual(encoded({"z": True, "a": "東京"}), '{"a":"東京","z":true}\n'.encode())
        self.assertNotEqual(encoded({"value": True}), encoded({"value": 1}))

    def test_wrong_types_unknown_fields(self):
        span = {"document_id": "d", "version": "r1", "start": 0, "end": 1}
        for changes in [{"end": True}, {"end": 1.0}, {"extra": 1}]:
            with self.subTest(changes=changes), self.assertRaises(Invalid):
                validate("span", {**span, **changes})
        with self.assertRaises(Invalid):
            validate("adapter-request", {"schema": "cgb.adapter-request.v1", "id": "r1", "operation": "describe", "payload": {"gold": [1]}})

    def test_label_binding(self):
        public = workload(read_json(ROOT / 'datasets/synthetic/smoke-workload.json'))
        oracle = read_json(ROOT / 'datasets/synthetic/smoke-labels.json')
        labels(oracle, public)
        public['documents'][0]['text'] += 'tamper'
        with self.assertRaises(Invalid):
            labels(oracle, public)

    def test_duplicate_task(self):
        public = read_json(ROOT / 'datasets/synthetic/smoke-workload.json')
        public['tasks'].append(public['tasks'][0])
        with self.assertRaises(Invalid):
            workload(public)


class Artifacts(unittest.TestCase):
    def test_empty_manifest_does_not_admit_symlink_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            original=root/'original';original.mkdir()
            seal(original,'run')
            alias=root/'alias';alias.symlink_to(original,target_is_directory=True)
            with self.assertRaises(Invalid):verify(alias)

    def test_seal_tamper_and_extra(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(root/'value.json', {'count': 1})
            seal(root, 'run')
            verify(root)
            (root/'value.json').write_bytes(b'{}\n')
            with self.assertRaises(Invalid): verify(root)

    def test_path_escape_and_symlink(self):
        from cgb.evidence import contained
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(root/'a.json', {'a': 1})
            (root/'b.json').symlink_to(root/'a.json')
            for path in ['../a.json', '/a.json', 'b.json', './a.json', 'x//a.json']:
                with self.subTest(path=path), self.assertRaises(Invalid): contained(root, path)

    def test_output_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'x.json'
            write_json(path, {'value': 1})
            with self.assertRaises(Invalid): write_json(path, {'value': 2})
            self.assertEqual(read_json(path), {'value': 1})


class Retention(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.counter = Counter(ROOT/'.cgb/tokenizer')
        cls.public = read_json(ROOT/'datasets/synthetic/smoke-workload.json')

    def test_unicode_span(self):
        task = self.public['tasks'][2]
        document = self.public['documents'][2]
        block = {'text': '🚀', 'citations': [{'document_id': document['id'], 'version': document['version'], 'start': 3, 'end': 4}]}
        result = verify_context([block], self.public, task, 512, self.counter)
        self.assertFalse(result['violations'])
        self.assertEqual(len(result['spans']), 1)
        block['citations'][0]['end'] = 5
        self.assertIn('fabricated-source-span', verify_context([block],self.public,task,512,self.counter)['violations'])

    def test_matching_id_omits_evidence(self):
        from cgb.adapters import Engine
        document = self.public['documents'][0]
        task = self.public['tasks'][0]
        block = {'text': 'Retry', 'citations': [{'document_id':document['id'],'version':'r1','start':0,'end':5}]}
        result = verify_context([block], self.public, task, 512, self.counter)
        oracle = read_json(ROOT/'datasets/synthetic/smoke-labels.json')['tasks'][0]
        self.assertEqual(covered_units(oracle['units'],result['spans']), [])
        complete = Engine.block(document)
        count = self.counter.count(__import__('cgb.render',fromlist=['render']).render([complete]))
        self.assertNotIn('budget-overrun',verify_context([complete],self.public,task,count,self.counter)['violations'])
        self.assertIn('budget-overrun',verify_context([complete],self.public,task,count-1,self.counter)['violations'])

    def test_stale_and_denied(self):
        from cgb.adapters import Engine
        block = Engine.block(self.public['documents'][-1])
        self.assertIn('unauthorized-or-deleted-source',verify_context([block],self.public,self.public['tasks'][0],512,self.counter)['violations'])
        block = Engine.block(self.public['documents'][0]); block['citations'][0]['version']='old'
        self.assertIn('stale-source',verify_context([block],self.public,self.public['tasks'][0],512,self.counter)['violations'])

    def test_alternatives_and_split_spans(self):
        span = {'document_id':'d','version':'r1','start':0,'end':4}
        units = [{'id':'fact','kind':'support','alternatives':[[span],[{**span,'document_id':'alternative'}]]}]
        delivered = [{**span,'end':2},{**span,'start':2},span]
        self.assertEqual(len(covered_units(units,delivered)),1)

    def test_token_count_known(self):
        self.assertEqual(self.counter.count(''),0)
        self.assertEqual(self.counter.count('hello world'),2)
        self.assertEqual(self.counter.count('a'*10000),1250)
        self.assertEqual(self.counter.count('<|endoftext|>'),7)

    def test_bpe_heap_matches_slow_oracle(self):
        import random
        rng = random.Random(7)
        for _ in range(200):
            raw = ''.join(rng.choice('abcd 123é東京🚀') for _ in range(rng.randrange(1,70))).encode()
            parts = [bytes([byte]) for byte in raw]
            while len(parts)>1:
                choices = [(self.counter.ranks.get(parts[i]+parts[i+1],float('inf')),i) for i in range(len(parts)-1)]
                rank, position = min(choices)
                if rank==float('inf'): break
                parts[position:position+2]=[parts[position]+parts[position+1]]
            self.assertEqual(self.counter._piece(raw),len(parts))


class Statistics(unittest.TestCase):
    def test_domain_weighting(self):
        rows=[]
        for domain, values in {'science':[1,0,0],'code':[1],'business':[.5]}.items():
            for value in values:
                rows.append({'domain':domain,'recall':value,'covered_units':int(value*2),'gold_units':2,'status':'ok','violations':[],'tokens':1,'query_ms':1,'startup_ms':1})
        suite={'domains':['science','code','business'],'mode':'evidence'}
        result=reduce_rows(rows,suite)
        self.assertAlmostEqual(result['evidence_score'],61.11111111111111)
        suite['domains'].append('missing')
        self.assertIsNone(reduce_rows(rows,suite)['evidence_score'])

    def test_wilson_zero_not_zero(self):
        self.assertAlmostEqual(wilson(0,30)[1],.11351339317396874)

    def test_cluster_floor_and_weighting(self):
        rows=[]
        for task,baseline,candidate in [('a',0,1),('b',0,0),('c',0,0)]:
            for adapter,value in [('baseline',baseline),('candidate',candidate)]:
                rows.append({'adapter':adapter,'task':task,'recall':value,'domain':'d','cluster':'first' if task!='a' else 'second'})
        result=paired_recall(rows,'baseline','candidate',['d'],0)
        self.assertAlmostEqual(result['difference'],100/3)
        self.assertIsNone(result['ci95'])
        self.assertEqual(result['clusters'],{'d':2})

    def test_cluster_bootstrap_is_deterministic(self):
        rows=[]
        for task in range(8):
            for adapter,value in [('baseline',0),('candidate',task/7)]:
                rows.append({'adapter':adapter,'task':str(task),'recall':value,'domain':'d','cluster':str(task)})
        first=paired_recall(rows,'baseline','candidate',['d'],7,draws=1000)
        self.assertEqual(first,paired_recall(rows*3,'baseline','candidate',['d'],7,draws=1000))
        self.assertEqual(first['clusters'],{'d':8})
        self.assertAlmostEqual(first['difference'],50)
        self.assertIsNotNone(first['ci95'])


class Baselines(unittest.TestCase):
    def test_bm25_length_normalization_ties_and_real_query(self):
        from cgb.adapters import Engine
        documents={'a':{'text':'x'},'b':{'text':'x other other other'},'c':{'text':'x'}}
        engine=Engine('bm25',None)
        # Same positive IDF and term frequency: shorter a/c outrank b; a wins the tie.
        self.assertEqual(engine.ranking(documents,'x'),['a','c','b'])
        self.assertEqual(engine.ranking(documents,'other'),['b'])
        self.assertEqual(engine.ranking(documents,'absent'),[])


if __name__ == '__main__': unittest.main()
