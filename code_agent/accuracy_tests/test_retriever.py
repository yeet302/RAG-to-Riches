import unittest
from pathlib import Path
from retriever import FixtureRetriever, FIXTURE_SHA

class RetrieverTests(unittest.TestCase):
    def setUp(self):
        self.r = FixtureRetriever(Path(__file__).parent/'fixtures'/'bug_01')
    def test_tools_return_fixture_source(self):
        self.assertEqual(self.r.list_repository_files()['files'], ['service.py'])
        hits = self.r.search_code('random fail',ref=FIXTURE_SHA)
        self.assertEqual(hits[0]['path'], 'service.py')
        source = self.r.read_source('service.py',start_line=8,end_line=9,ref=FIXTURE_SHA)
        self.assertEqual([x['line'] for x in source['lines']],[8,9])
    def test_no_path_escape(self):
        with self.assertRaises(ValueError):
            self.r.read_source('../secret')

if __name__ == '__main__': unittest.main()
