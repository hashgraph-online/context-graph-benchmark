import importlib.util
from pathlib import Path
import unittest

from cgb.graph_backends import GraphBackend, distribution_identity


class OptionalGraphLibraries(unittest.TestCase):
    def exercise(self, name):
        if importlib.util.find_spec(name) is None:
            self.skipTest(name+' optional dependency is not installed')
        documents={'a':{},'b':{},'c':{}}
        edges=[{'from':'a','to':'b','kind':'requires'},
               {'from':'b','to':'c','kind':'contradicts'},
               {'from':'c','to':'a','kind':'requires'}]
        backend=GraphBackend(name,documents,edges)
        self.assertEqual(backend.closure('a'),['a','b','c'])
        self.assertIsNone(GraphBackend(name,{'a':{}},edges).closure('a'))
        self.assertIsNone(backend.closure('missing'))
        self.assertTrue(distribution_identity(name)['files'])

    def test_networkx_native_cycle_and_denied_dependency(self):self.exercise('networkx')


if __name__=='__main__':unittest.main()
