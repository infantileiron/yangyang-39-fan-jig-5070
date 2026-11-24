import sys
import types
import unittest
from collections import defaultdict
from statistics import mean

# 本地环境可能没有 scipy；分析逻辑只用 scipy.stats.f.sf，注入一个确定性桩，
# 容器内有真实 scipy 时本测试同样能跑（桩仅在模块缺失时生效）。
try:
    import scipy  # noqa: F401
except ImportError:
    scipy = types.ModuleType('scipy')
    stats = types.ModuleType('scipy.stats')

    class _F:
        @staticmethod
        def sf(statistic, dfn, dfd):
            return 0.5 if statistic is not None else float('nan')

    stats.f = _F()
    scipy.stats = stats
    sys.modules['scipy'] = scipy
    sys.modules['scipy.stats'] = stats

sys.path.insert(0, '/workspace/backend')

from src.domain.analysis import analyze, eligible_choices, seed  # noqa: E402
from src.domain.corrections import (  # noqa: E402
    correction_idem_key, resolve_observations, robust_review, validate_correction)
from src.utils.keys import content_key  # noqa: E402


def row(trial, cell=0, warp=2.0, energy=10.0):
    cells = (("低风", "直边夹"), ("低风", "弧边夹"), ("高风", "直边夹"), ("高风", "弧边夹"))
    speed, jig = cells[cell]
    return dict(trial=trial, speed=speed, jig=jig, warp=warp, energy=energy)


def balanced_rows():
    # 每格 3 片，共 12 片；低风·弧边夹翘边均值最小且能耗在护栏内
    plan = {
        0: [('A1', 3.0, 8.0), ('A2', 3.2, 8.4), ('A3', 3.4, 8.2)],
        1: [('B1', 2.0, 9.0), ('B2', 2.2, 9.2), ('B3', 2.4, 9.4)],
        2: [('C1', 4.0, 14.0), ('C2', 4.2, 14.4), ('C3', 4.4, 14.2)],
        3: [('D1', 1.0, 17.0), ('D2', 1.2, 17.2), ('D3', 1.4, 17.4)],
    }
    return [row(trial, cell, warp, energy) for cell, items in plan.items()
            for trial, warp, energy in items]


def verified(trial, basis_row, corrected):
    return {'id': 1, 'trial': trial, 'basis': {k: basis_row[k] for k in ('warp', 'energy')},
            'corrected': corrected, 'basis_key': content_key(basis_row), 'status': 'verified'}


def pending(cid, trial, basis_row, corrected):
    return {'id': cid, 'trial': trial, 'basis': {k: basis_row[k] for k in ('warp', 'energy')},
            'corrected': corrected, 'basis_key': content_key(basis_row), 'status': 'pending'}


class CorrectionValidationTests(unittest.TestCase):
    def test_requires_original_reading(self):
        with self.assertRaisesRegex(ValueError, '原读数'):
            validate_correction({'trial': 'A1', 'basis': {}, 'corrected': {'warp': 1.0}})

    def test_rejects_no_change(self):
        with self.assertRaisesRegex(ValueError, '完全相同'):
            validate_correction({'trial': 'A1', 'basis': {'warp': 2.0}, 'corrected': {'warp': 2.0}})

    def test_rejects_cell_change_via_correction(self):
        with self.assertRaisesRegex(ValueError, '风档和夹具'):
            validate_correction({'trial': 'A1', 'basis': {'warp': 2.0},
                                 'corrected': {'warp': 2.1, 'speed': '高风'}})

    def test_idem_key_stable_and_distinct(self):
        basis = {'warp': 2.0, 'energy': 10.0}
        key1 = correction_idem_key('A1', basis)
        key2 = correction_idem_key('A1', dict(basis))
        key3 = correction_idem_key('A2', basis)
        self.assertEqual(key1, key2)
        self.assertNotEqual(key1, key3)

    def test_zero_reading_is_a_valid_basis(self):
        trial, basis, corrected = validate_correction(
            {'trial': 'A1', 'basis': {'warp': 0, 'energy': 10.0}, 'corrected': {'warp': 0.4}})
        self.assertEqual(basis, {'warp': 0.0, 'energy': 10.0})
        self.assertEqual(corrected, {'warp': 0.4})

    def test_single_field_change_is_accepted(self):
        trial, basis, corrected = validate_correction(
            {'trial': 'A1', 'basis': {'warp': 2.0, 'energy': 10.0}, 'corrected': {'energy': 11.0}})
        self.assertEqual(corrected, {'energy': 11.0})

    def test_non_numeric_reading_rejected(self):
        with self.assertRaisesRegex(ValueError, '数字'):
            validate_correction({'trial': 'A1', 'basis': {'warp': 'x'}, 'corrected': {'warp': 1.0}})

    def test_requires_corrected_reading(self):
        with self.assertRaisesRegex(ValueError, '更正后'):
            validate_correction({'trial': 'A1', 'basis': {'warp': 2.0}, 'corrected': {}})


class DuplicateLedgerTests(unittest.TestCase):
    def test_duplicate_without_matching_verified_correction_stops_analysis(self):
        rows = balanced_rows()
        rows.append(dict(rows[3]))  # B1 第二条完工记录（补抄耗电后产生）
        with self.assertRaisesRegex(ValueError, 'B1'):
            analyze(rows, {'energy_limit': 15, 'alpha': 0.05}, [])

    def test_verified_correction_with_basis_merges_duplicates_into_one(self):
        rows = balanced_rows()
        original = next(item for item in rows if item['trial'] == 'B1')
        rows.append(dict(original))  # 同一试片两条完工记录
        snapshot = [verified('B1', original, {'energy': 9.8})]
        result = analyze(rows, {'energy_limit': 15, 'alpha': 0.05}, snapshot)
        # 合并后仍是 12 片，不是 13 条
        self.assertEqual(result['metrics'][0], {'label': '完成试片', 'value': 12, 'unit': '片'})
        b1_cell = next(item for item in result['rows'] if item['label'] == '低风·弧边夹')
        self.assertAlmostEqual(b1_cell['energy'], round(mean([9.8, 9.2, 9.4]), 3), places=6)

    def test_verified_correction_with_wrong_basis_does_not_merge(self):
        rows = balanced_rows()
        original = next(item for item in rows if item['trial'] == 'B1')
        rows.append(dict(original))
        wrong_basis = dict(original, energy=12.3)
        snapshot = [{'id': 9, 'trial': 'B1', 'basis': {'warp': 2.0, 'energy': 12.3},
                     'corrected': {'energy': 9.8}, 'basis_key': content_key(wrong_basis),
                     'status': 'verified'}]
        with self.assertRaisesRegex(ValueError, 'B1'):
            resolve_observations(rows, snapshot)

    def test_pending_correction_never_replaces_primary_reading(self):
        rows = balanced_rows()
        original = next(item for item in rows if item['trial'] == 'B1')
        snapshot = [pending(2, 'B1', original, {'warp': 9.9})]
        resolved = resolve_observations(rows, snapshot)
        b1 = next(item for item in resolved if item['trial'] == 'B1')
        self.assertEqual(b1['warp'], 2.0)

    def test_late_old_record_cannot_overwrite_verified_correction(self):
        # 已核实更正生效后，即使又送来同试片的另一条旧记录，主读数仍来自更正
        rows = balanced_rows()
        original = next(item for item in rows if item['trial'] == 'B1')
        late = dict(original, energy=15.0)
        rows.append(late)
        snapshot = [verified('B1', original, {'energy': 9.8})]
        resolved = resolve_observations(rows, snapshot)
        b1 = next(item for item in resolved if item['trial'] == 'B1')
        self.assertEqual(b1['energy'], 9.8)
        self.assertEqual(len(resolved), 12)


class RobustnessTests(unittest.TestCase):
    def setUp(self):
        self.rows = balanced_rows()
        self.params = {'energy_limit': 15, 'alpha': 0.05}

    def test_no_pending_corrections_is_robust(self):
        review = robust_review(self.rows, [], self.params)
        self.assertTrue(review['robust'])
        self.assertEqual(review['retest_trials'], [])

    def test_same_recommendation_across_readings_still_recommends(self):
        # B3 的两种可能读数都不会改变获胜组合 -> 照常推荐，不要求复测
        original = next(item for item in self.rows if item['trial'] == 'B3')
        snapshot = [pending(1, 'B3', original, {'warp': 2.5}),
                    pending(2, 'B3', original, {'warp': 2.6})]
        review = robust_review(self.rows, snapshot, self.params)
        self.assertTrue(review['robust'])
        self.assertEqual(review['retest_trials'], [])
        self.assertEqual(review['scenario_count'], 2)
        result = analyze(self.rows, self.params, snapshot)
        self.assertIn('低风·弧边夹', result['headline'])

    def test_changing_recommendation_flags_trial_for_retest(self):
        # B1 翘边被改到 5.9 时，低风·弧边夹均值反超低风·直边夹，推荐翻转
        original = next(item for item in self.rows if item['trial'] == 'B1')
        snapshot = [pending(1, 'B1', original, {'warp': 5.9}),
                    pending(2, 'B1', original, {'warp': 2.1})]
        review = robust_review(self.rows, snapshot, self.params)
        self.assertFalse(review['robust'])
        self.assertEqual(review['retest_trials'], ['B1'])
        self.assertEqual(set(review['possible_winners']),
                         {'低风·直边夹', '低风·弧边夹'})
        result = analyze(self.rows, self.params, snapshot)
        # 主结论仍按已核实读数推荐，但结果页数据里点名该复测的试片
        self.assertIn('低风·弧边夹', result['headline'])
        self.assertEqual(result['robustness']['retest_trials'], ['B1'])

    def test_identical_pending_readings_are_not_conflicts(self):
        original = next(item for item in self.rows if item['trial'] == 'B3')
        snapshot = [pending(1, 'B3', original, {'warp': 2.9}),
                    pending(2, 'B3', original, {'warp': 2.9})]
        review = robust_review(self.rows, snapshot, self.params)
        self.assertTrue(review['robust'])
        self.assertEqual(review['ambiguous_trials'], [])


class SnapshotTests(unittest.TestCase):
    def test_corrections_arriving_after_queue_are_not_in_snapshot(self):
        rows = balanced_rows()
        original = next(item for item in rows if item['trial'] == 'B1')
        # 排队时只有 A 试片的更正；B1 的更正是排队后才到的
        queued_snapshot = [pending(1, 'A1', next(r for r in rows if r['trial'] == 'A1'),
                                   {'warp': 3.1})]
        late_correction = verified('B1', original, {'warp': 5.9})
        # 重跑仍用原先那批读数：late_correction 不传给 analyze
        result_before = analyze(rows, {'energy_limit': 15, 'alpha': 0.05}, queued_snapshot)
        result_rerun = analyze(rows, {'energy_limit': 15, 'alpha': 0.05}, list(queued_snapshot))
        self.assertEqual(result_before['headline'], result_rerun['headline'])
        self.assertNotIn('B1', result_rerun['robustness'].get('retest_trials', []))
        # 下次分析带上新快照后，晚到更正才生效
        next_snapshot = queued_snapshot + [late_correction]
        resolved = resolve_observations(rows, next_snapshot)
        self.assertEqual(next(item for item in resolved if item['trial'] == 'B1')['warp'], 5.9)

    def test_seed_data_stays_balanced_and_resolvable(self):
        rows = seed()
        # 种子本身四格各 7 片，可直接计算
        result = analyze(rows, {'energy_limit': 15, 'alpha': 0.05}, [])
        self.assertIn('低风·弧边夹', result['headline'])
        groups = defaultdict(list)
        for item in rows:
            groups[(item['speed'], item['jig'])].append(item)
        self.assertEqual({len(value) for value in groups.values()}, {7})


if __name__ == '__main__':
    unittest.main(verbosity=2)
