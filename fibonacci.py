def fibonacci(n):
    """Return the nth Fibonacci number (0-indexed: F0=0, F1=1)."""
    if not isinstance(n, int) or n < 0:
        raise ValueError("n must be a non-negative integer")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


if __name__ == "__main__":
    # Quick sanity check: F0..F10 = 0,1,1,2,3,5,8,13,21,34,55
    expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55]
    for i, exp in enumerate(expected):
        got = fibonacci(i)
        assert got == exp, f"F{i}: got {got}, expected {exp}"
    print("All checks passed:", [fibonacci(i) for i in range(11)])
