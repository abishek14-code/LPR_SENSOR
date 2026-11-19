"""Synthetic fixtures test software only. No fixture is an LTspice result."""
from pathlib import Path
import csv
import json
import sys
import tempfile
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from reference_checks import values, analytic_current, validate
from generate_noise import generate
from export_trace import read_trace, convert, phase
from analyze_trace import analyze
from check_sources import check

HEADER = 'time\tV(req)\tV(actual)\tV(i_meas)\tV(i_true)\tV(relay)\n'
TEXT = HEADER+'0\t0\t0\t0\t0\t1\n1\t.01\t.01\t1e-7\t1e-7\t1\n'


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, content=TEXT):
        path = self.root/'fixture.txt'
        path.write_text(content)
        return path

    def test_reference_values(self):
        v = values()
        self.assertAlmostEqual(v['tau_s'], 10/11)
        self.assertAlmostEqual(v['i_initial_A'], 1e-6)
        self.assertAlmostEqual(v['i_steady_A'], 1e-6/11)
        self.assertEqual(v['rdc_ohm'], 110000)

    def test_ode_agreement(self):
        report = validate()
        self.assertLess(report['max_ode_vs_closed_form_error_A'], 1e-12)
        self.assertFalse(report['ltspice_executed'])

    def test_nonlinear_challenge(self):
        cases = validate()['nonlinear_steady_state']
        self.assertLess(abs(cases[0]['apparent_rdc_ohm']/110000-1), .001)
        self.assertLess(cases[-1]['apparent_rdc_ohm'], cases[0]['apparent_rdc_ohm'])

    def test_current_limits(self):
        self.assertAlmostEqual(float(analytic_current(0)), 1e-6)
        self.assertAlmostEqual(float(analytic_current(100)), 1e-6/11)
        self.assertAlmostEqual(float(analytic_current(0, e=-.01)), -1e-6)

    def test_bad_parameters(self):
        with self.assertRaises(ValueError):
            values(ru=0)

    def test_seeded_pwl(self):
        a, b = self.root/'a.txt', self.root/'b.txt'
        generate(a, duration=1)
        generate(b, duration=1)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        data = np.loadtxt(a)
        y = data[:,1]
        rms = np.sqrt(np.mean((y[:-1]**2+y[:-1]*y[1:]+y[1:]**2)/3))
        self.assertAlmostEqual(rms, 1, places=10)

    def test_text_import(self):
        names, rows, count = read_trace(self.source())
        self.assertEqual(count,1)
        self.assertEqual(names[1],'v(req)')
        self.assertEqual(rows[1][3],1e-7)

    def test_ascii_raw_import(self):
        raw = 'Title: synthetic\nFlags: real\nNo. Variables: 2\nNo. Points: 2\nVariables:\n0 time time\n1 V(req) voltage\nValues:\n0 0\n0\n1 1\n0.01\n'
        names, rows, count = read_trace(self.source(raw))
        self.assertEqual(rows, [[0,0],[1,.01]])

    def test_utf16_import(self):
        path = self.root/'utf16.txt'
        path.write_text(TEXT, encoding='utf-16')
        self.assertEqual(read_trace(path)[2],1)

    def test_step_selection_required(self):
        source = self.source(TEXT+'Step Information: RU=20000 (Run: 2/2)\n0 0 0 0 0 1\n1 .01 .01 2e-7 2e-7 1\n')
        with self.assertRaisesRegex(ValueError, 'steps found'):
            read_trace(source)
        self.assertEqual(read_trace(source,2)[1][-1][3],2e-7)
        with self.assertRaises(ValueError):
            read_trace(source,3)

    def test_missing_trace_rejected(self):
        source = self.source('time V(req)\n0 0\n1 .01\n')
        with self.assertRaisesRegex(ValueError, 'Missing traces'):
            convert(source, self.root/'out.csv','03_healthy')

    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):
            read_trace(self.source(TEXT.replace('1e-7', 'nan')))

    def test_duplicate_time_rejected(self):
        with self.assertRaisesRegex(ValueError, 'strictly increase'):
            read_trace(self.source(TEXT+'1 .01 .01 1e-7 1e-7 1\n'))

    def test_binary_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Binary'):
            read_trace(self.source('Title: test\nBinary:\n'))

    def test_csv_schema_and_no_oracle_decisions(self):
        output = self.root/'output.csv'
        metadata = convert(self.source(), output, '03_healthy')
        with output.open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[1]['current'], '1e-07')
        self.assertEqual(rows[1]['diagnostic'], '')
        self.assertEqual(rows[1]['fault'], '')
        self.assertEqual(metadata['samples'],2)
        self.assertTrue(output.with_suffix('.json').exists())

    def test_fixed_stimulus_phases(self):
        self.assertEqual(phase(3,'03_healthy'), 'A1')
        self.assertEqual(phase(21,'03_healthy'), 'B')
        self.assertEqual(phase(39,'03_healthy'), 'A2')
        self.assertEqual(phase(0,'00_cell_analytical'), 'BASELINE')

    def test_analysis_on_synthetic_fixture(self):
        t = np.linspace(0,12,12001)
        current = np.where(t<1.000001,0,analytic_current(np.maximum(t-1.000001,0)))
        e = np.where(t<1.000001,0,.01)
        source = self.root/'synthetic_fixture.txt'
        np.savetxt(source,np.column_stack([t,e,e,current,current,np.ones_like(t)]),
                   header=HEADER.strip(),comments='',delimiter='\t')
        output=self.root/'synthetic_fixture.csv'
        convert(source,output,'00_cell_analytical')
        report=analyze(output,self.root/'synthetic_plots')
        self.assertTrue(report['baseline_acceptance_check'])
        self.assertLess(abs(report['tau_fitted_s']-10/11),1e-5)
        self.assertTrue((self.root/'synthetic_plots/waveforms.png').is_file())

    def test_source_dependencies(self):
        self.assertEqual(len(check()['cases']),21)


if __name__ == '__main__':
    unittest.main(verbosity=2)
