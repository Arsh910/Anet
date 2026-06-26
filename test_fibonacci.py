"""Unit tests for the fibonacci function in fibonacci.py."""
import pytest

from fibonacci import fibonacci


class TestBaseCases:
    """Verify the well-known base and early Fibonacci values."""

    @pytest.mark.parametrize("n,expected", [(0, 0), (1, 1), (2, 1), (3, 2)])
    def test_small_values(self, n, expected):
        assert fibonacci(n) == expected

    def test_first_eleven_match_sequence(self):
        expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55]
        assert [fibonacci(i) for i in range(11)] == expected


class TestRecurrence:
    """F(n) = F(n-1) + F(n-2) must hold for all n >= 2."""

    @pytest.mark.parametrize("n", range(2, 40))
    def test_recurrence_holds(self, n):
        assert fibonacci(n) == fibonacci(n - 1) + fibonacci(n - 2)

    def test_known_larger_value(self):
        # F(50) = 12586269025
        assert fibonacci(50) == 12586269025


class TestInputValidation:
    """Invalid inputs must raise ValueError."""

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="non-negative integer"):
            fibonacci(-1)

    def test_float_raises(self):
        with pytest.raises(ValueError, match="non-negative integer"):
            fibonacci(5.0)

    @pytest.mark.parametrize("bad", ["10", None, [3]])
    def test_non_integer_raises(self, bad):
        with pytest.raises(ValueError, match="non-negative integer"):
            fibonacci(bad)

    def test_bool_is_integer(self):
        # bool is a subclass of int; True -> 1, False -> 0
        assert fibonacci(True) == 1
        assert fibonacci(False) == 0


class TestEdgeCases:
    """Boundary and type behaviors."""

    def test_zero(self):
        assert fibonacci(0) == 0

    def test_one(self):
        assert fibonacci(1) == 1

    def test_returns_int(self):
        assert isinstance(fibonacci(10), int)

    def test_large_n_does_not_recurse_deeply(self):
        # If the implementation were recursive, this would hit RecursionError.
        assert fibonacci(1000) == fibonacci(999) + fibonacci(998)
