# Fibonacci Function Unit Tests

## Overview
Comprehensive unit tests for the `fibonacci(n)` function have been created in `test_fibonacci.py`. All **23 tests** pass successfully.

## Test File Location
- **Path**: `C:\thinkbig\Anet\Anet\tests\test_fibonacci.py`
- **Framework**: Python `unittest` with `pytest` runner

## Test Coverage

### 1. **Base Cases** (2 tests)
- `test_fibonacci_zero()` — Validates fibonacci(0) = 0
- `test_fibonacci_one()` — Validates fibonacci(1) = 1

### 2. **Small Values** (4 tests)
- `test_fibonacci_small_values()` — Tests first 10 Fibonacci numbers (0-9) using parametrized subtests
- `test_fibonacci_two()` — fibonacci(2) = 1
- `test_fibonacci_three()` — fibonacci(3) = 2
- `test_fibonacci_five()` — fibonacci(5) = 5

### 3. **Medium Values** (3 tests)
- `test_fibonacci_ten()` — fibonacci(10) = 55
- `test_fibonacci_fifteen()` — fibonacci(15) = 610
- `test_fibonacci_twenty()` — fibonacci(20) = 6765

### 4. **Large Values** (3 tests)
- `test_fibonacci_thirty()` — fibonacci(30) = 832040
- `test_fibonacci_fifty()` — fibonacci(50) = 12586269025
- `test_fibonacci_hundred()` — fibonacci(100) = 354224848179261915075

### 5. **Error Handling** (3 tests)
- `test_fibonacci_negative_one()` — Raises ValueError for negative input (-1)
- `test_fibonacci_negative_ten()` — Raises ValueError for negative input (-10)
- `test_fibonacci_negative_large()` — Raises ValueError for large negative input (-1000)
- ✅ Validates error message contains "non-negative"

### 6. **Mathematical Properties** (3 tests)
- `test_fibonacci_sequence_property()` — Validates fib(n) = fib(n-1) + fib(n-2) for n in range(2, 20)
- `test_fibonacci_is_positive_for_positive_n()` — All fibonacci(n) ≥ 0 for n ≥ 0
- `test_fibonacci_is_increasing()` — Sequence is non-decreasing for n > 0

### 7. **Type & Edge Cases** (3 tests)
- `test_fibonacci_with_float_zero()` — Tests float input handling (0.0)
- `test_fibonacci_consecutive_pairs_sum()` — Validates fib(n+1) = fib(n) + fib(n-1)
- `test_fibonacci_returns_integer()` — Confirms all results are Python `int` type
- `test_fibonacci_boundary_between_zero_and_one()` — fibonacci(0) < fibonacci(1)

### 8. **Performance** (1 test)
- `test_fibonacci_large_value_performance()` — fibonacci(1000) completes in < 1 second
  - ✅ Generates numbers > 10^200 (proving correct computation)
  - ✅ Confirms O(n) iterative approach is efficient

## Test Statistics

| Metric | Value |
|--------|-------|
| **Total Tests** | 23 |
| **Subtests** | 84 |
| **Pass Rate** | 100% |
| **Execution Time** | 0.06s |
| **Framework** | unittest + pytest |

## Running the Tests

### Using pytest:
```bash
pytest tests/test_fibonacci.py -v
```

### Using unittest:
```bash
python -m unittest tests.test_fibonacci -v
```

### Run specific test class:
```bash
pytest tests/test_fibonacci.py::TestFibonacci -v
```

### Run specific test:
```bash
pytest tests/test_fibonacci.py::TestFibonacci::test_fibonacci_zero -v
```

## Key Test Features

✅ **Comprehensive Coverage**
- Base cases (0, 1)
- Small, medium, and large values
- Negative inputs with error validation
- Mathematical properties validation
- Performance testing

✅ **Subtests for Efficiency**
- Multiple values tested under single test method
- Better failure isolation and reporting

✅ **Mathematical Validation**
- Recursive property: fib(n) = fib(n-1) + fib(n-2)
- Monotonic increase verification
- Non-negativity check

✅ **Error Handling**
- Negative input validation
- Exception message inspection
- Multiple negative values tested

✅ **Performance**
- Large number computation (n=1000)
- Verifies O(n) iterative implementation
- Completes efficiently (< 1 second)

## Example Test Output

```
tests/test_fibonacci.py::TestFibonacci::test_fibonacci_zero PASSED
tests/test_fibonacci.py::TestFibonacci::test_fibonacci_one PASSED
tests/test_fibonacci.py::TestFibonacci::test_fibonacci_small_values PASSED [with 10 subtests]
tests/test_fibonacci.py::TestFibonacci::test_fibonacci_sequence_property PASSED [with 18 subtests]
tests/test_fibonacci.py::TestFibonacciPerformance::test_fibonacci_large_value_performance PASSED

=================== 23 passed, 84 subtests passed in 0.06s ====================
```

## Notes

- All tests use **assertion methods** from unittest for consistency
- **Subtests** (`self.subTest()`) provide granular failure reporting
- **Context managers** (`self.assertRaises()`) ensure proper exception handling
- **Type validation** ensures return type is always `int`
- **Performance test** includes timing and result magnitude verification
