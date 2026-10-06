import unittest
import numpy as np
from confirmatory.campaign import jobs, job_id, served_metrics, METHODS
from confirmatory.analyse import tost, summary

class ConfirmatoryPhaseTests(unittest.TestCase):
    def test_design(self):
        js=jobs();ids=[job_id(j)+"_"+m for j in js for m in j["methods"]]
        self.assertEqual(len(ids),2660);self.assertEqual(len(set(ids)),2660)
        self.assertEqual({j['seed'] for j in js},set(range(9001,9041)))
    def test_one_factor_at_a_time(self):
        a,b=METHODS['CENTRAL_FB'],METHODS['CENTRAL_FOG_FB']
        self.assertEqual(a[:3],b[:3]);self.assertNotEqual(a[3],b[3])
        a,b=METHODS['CENTRAL_FOG'],METHODS['CENTRAL_FOG_FB']
        self.assertEqual(a[0],b[0]);self.assertEqual(a[2:],b[2:]);self.assertNotEqual(a[1],b[1])
        self.assertEqual(METHODS['GREEDY_FOG'][3],'fog')
    def test_served_denominator_and_end(self):
        R=[dict(id=i,priority=i+1,a=0,b=10,urgent=True,static=False) for i in range(3)]
        log=[dict(ev='service_start',r=i,t=5,team=0,outcome='served' if i!=1 else 'absent') for i in range(3)]
        log += [dict(ev='service_end',r=0,t=9,team=0,outcome='served'),dict(ev='service_end',r=1,t=9,team=0,outcome='absent')]
        r=served_metrics(dict(requests=R),log)
        self.assertAlmostEqual(r['priority_served_pct'],100/6);self.assertEqual(r['unfinished_served'],1)
    def test_equivalence_not_nonsignificance(self):
        a=np.array([-.1,.1]*20);b=np.array([-10,10]*20)
        self.assertTrue(tost(a)['equivalent']);self.assertFalse(tost(b)['equivalent'])
        self.assertEqual(summary(b)['p'],1.0)
    def test_constant_boundary(self):
        self.assertTrue(tost(np.zeros(40))['equivalent'])
        self.assertFalse(tost(np.repeat(1.5,40))['equivalent'])

if __name__=='__main__':unittest.main()
