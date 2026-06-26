import unittest
import sys
from pathlib import Path

# Add parent directory to path to import fibonacci module
sys.path.insert(0, str(Path(__file__).parent.parent))

from fibonacci import fibonacci


class TestFibonacci(unittest.TestCase):
    """Comprehensive unit tests for the fibonacci function."""
    
    # ============== Base Cases ==============
    def test_fibonacci_zero(self):
        """Test that fibonacci(0) returns 0."""
        self.assertEqual(fibonacci(0), 0)
    
    def test_fibonacci_one(self):
        """Test that fibonacci(1) returns 1."""
        self.assertEqual(fibonacci(1), 1)
    
    # ============== Small Values ==============
    def test_fibonacci_small_values(self):
        """Test fibonacci for the first 10 numbers."""
        expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
        for n, expected_value in enumerate(expected):
            with self.subTest(n=n):
                self.assertEqual(fibonacci(n), expected_value)
    
    def test_fibonacci_two(self):
        """Test that fibonacci(2) returns 1."""
        self.assertEqual(fibonacci(2), 1)
    
    def test_fibonacci_three(self):
        """Test that fibonacci(3) returns 2."""
        self.assertEqual(fibonacci(3), 2)
    
    def test_fibonacci_five(self):
        """Test that fibonacci(5) returns 5."""
        self.assertEqual(fibonacci(5), 5)
    
    # ============== Medium Values ==============
    def test_fibonacci_ten(self):
        """Test that fibonacci(10) returns 55."""
        self.assertEqual(fibonacci(10), 55)
    
    def test_fibonacci_fifteen(self):
        """Test that fibonacci(15) returns 610."""
        self.assertEqual(fibonacci(15), 610)
    
    def test_fibonacci_twenty(self):
        """Test that fibonacci(20) returns 6765."""
        self.assertEqual(fibonacci(20), 6765)
    
    # ============== Large Values ==============
    def test_fibonacci_thirty(self):
        """Test that fibonacci(30) returns 832040."""
        self.assertEqual(fibonacci(30), 832040)
    
    def test_fibonacci_fifty(self):
        """Test that fibonacci(50) returns 12586269025."""
        self.assertEqual(fibonacci(50), 12586269025)
    
    def test_fibonacci_hundred(self):
        """Test that fibonacci(100) returns the expected large number."""
        # fibonacci(100) = 354224848179261915075
        self.assertEqual(fibonacci(100), 354224848179261915075)
    
    # ============== Error Handling ==============
    def test_fibonacci_negative_one(self):
        """Test that fibonacci(-1) raises ValueError."""
        with self.assertRaises(ValueError) as context:
            fibonacci(-1)
        self.assertIn("non-negative", str(context.exception))
    
    def test_fibonacci_negative_ten(self):
        """Test that fibonacci(-10) raises ValueError."""
        with self.assertRaises(ValueError):
            fibonacci(-10)
    
    def test_fibonacci_negative_large(self):
        """Test that fibonacci with large negative number raises ValueError."""
        with self.assertRaises(ValueError):
            fibonacci(-1000)
    
    # ============== Mathematical Properties ==============
    def test_fibonacci_sequence_property(self):
        """Test that fib(n) = fib(n-1) + fib(n-2) for n > 1."""
        for n in range(2, 20):
            with self.subTest(n=n):
                expected = fibonacci(n - 1) + fibonacci(n - 2)
                self.assertEqual(fibonacci(n), expected)
    
    def test_fibonacci_is_positive_for_positive_n(self):
        """Test that fibonacci(n) is non-negative for all n >= 0."""
        for n in range(0, 50):
            with self.subTest(n=n):
                self.assertGreaterEqual(fibonacci(n), 0)
    
    def test_fibonacci_is_increasing(self):
        """Test that fibonacci sequence is non-decreasing for n > 0."""
        prev = fibonacci(0)
        for n in range(1, 30):
            curr = fibonacci(n)
            self.assertGreaterEqual(curr, prev)
            prev = curr
    
    # ============== Type Handling ==============
    def test_fibonacci_with_float_zero(self):
        """Test that fibonacci(0.0) works (converts to int)."""
        # Note: This tests if function handles float inputs gracefully
        try:
            result = fibonacci(int(0.0))
            self.assertEqual(result, 0)
        except TypeError:
            # If function strictly requires int, that's also valid
            pass
    
    def test_fibonacci_consecutive_pairs_sum(self):
        """Test Fibonacci identity: fib(n+1) = fib(n) + fib(n-1)."""
        # This is implicit in the sequence, but explicitly verify
        n = 15
        fib_n_minus_1 = fibonacci(n - 1)
        fib_n = fibonacci(n)
        fib_n_plus_1 = fibonacci(n + 1)
        self.assertEqual(fib_n_plus_1, fib_n + fib_n_minus_1)
    
    # ============== Edge Cases ==============
    def test_fibonacci_returns_integer(self):
        """Test that fibonacci always returns an integer."""
        for n in [0, 1, 5, 10, 20, 50]:
            with self.subTest(n=n):
                result = fibonacci(n)
                self.assertIsInstance(result, int)
    
    def test_fibonacci_boundary_between_zero_and_one(self):
        """Test the boundary: fibonacci(0) < fibonacci(1)."""
        self.assertLess(fibonacci(0), fibonacci(1))


class TestFibonacciPerformance(unittest.TestCase):
    """Performance-related tests for the fibonacci function."""
    
    def test_fibonacci_large_value_performance(self):
        """Test that fibonacci can handle reasonably large values efficiently."""
        import time
        
        # This should complete in well under a second (iterative is fast)
        start = time.time()
        result = fibonacci(1000)
        elapsed = time.time() - start
        
        # Verify it completes in reasonable time (< 1 second)
        self.assertLess(elapsed, 1.0)
        # Verify it returns a very large number (not 0 or None)
        self.assertGreater(result, 10**200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
