"""Tests for eval/cpcv.py split generation (pure logic, no training)."""

import numpy as np
import pytest

from eval.cpcv import cpcv_splits
from itertools import combinations


class TestCPCVSplits:
    def test_combination_count(self):
        splits = cpcv_splits(1200, n_groups=6, k=2, horizon=10, embargo=10)
        assert len(splits) == len(list(combinations(range(6), 2))) == 15

    def test_test_sets_are_disjoint_pairs_of_groups(self):
        n = 600
        splits = cpcv_splits(n, n_groups=6, k=2, horizon=5, embargo=0)
        S = n // 6
        for sp in splits:
            assert len(sp.test_idx) == 2 * S
            groups = set()
            for t in sp.test_idx:
                groups.add(t // S)
            assert groups == set(sp.combo)

    def test_no_train_test_overlap(self):
        splits = cpcv_splits(1200, n_groups=6, k=2, horizon=10, embargo=10)
        for sp in splits:
            assert not np.intersect1d(sp.train_idx, sp.test_idx).size

    def test_purge_removes_leaky_samples(self):
        """Training samples whose forward window [i+1, i+H] crosses into the
        test block must be excluded."""
        n = 600
        H = 10
        splits = cpcv_splits(n, n_groups=6, k=1, horizon=H, embargo=0)
        sp = splits[0]  # combo (0,), group 0 = [0, 100)
        test_min = sp.test_idx.min()  # 0 here, so purge zone is empty before
        # use a middle group instead
        sp_mid = [s for s in splits if s.combo == (3,)][0]
        t_min = sp_mid.test_idx.min()
        t_max = sp_mid.test_idx.max()
        # No train sample i with t_min - H <= i < t_max may exist
        leaky = sp_mid.train_idx[
            (sp_mid.train_idx >= t_min - H) & (sp_mid.train_idx < t_max)
        ]
        assert leaky.size == 0

    def test_embargo_zone_excluded(self):
        n = 600
        embargo = 25
        splits = cpcv_splits(n, n_groups=6, k=1, horizon=5, embargo=embargo)
        sp_mid = [s for s in splits if s.combo == (2,)][0]
        after = sp_mid.train_idx[sp_mid.train_idx > sp_mid.test_idx.max()]
        # First allowed sample after test end must be beyond the embargo zone
        assert after.size == 0 or after.min() > sp_mid.test_idx.max() + embargo

    def test_invalid_args_raise(self):
        with pytest.raises(ValueError):
            cpcv_splits(100, n_groups=5, k=5)
        with pytest.raises(ValueError):
            cpcv_splits(4, n_groups=6, k=2)

    def test_train_coverage_sane(self):
        """Train sets should retain most non-test samples when purge+embargo
        are small relative to group size."""
        n = 12_000
        splits = cpcv_splits(n, n_groups=6, k=2, horizon=10, embargo=10)
        S = n // 6
        for sp in splits:
            expected_max = n - 2 * S
            assert sp.train_idx.size >= expected_max - 2 * (10 + 10 + 1)
            assert sp.train_idx.size < n
