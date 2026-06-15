"""
demo.py - basic arithmetic utilities.

Exposes simple operations: add, sub, mul, div, plus a small dispatcher
(`calculate`) that looks operations up by name.
"""

from typing import Callable, Dict

__all__ = ["add", "sub", "mul", "div", "calculate", "subtotal", "subscribe"]


def add(a: float, b: float) -> float:
    """Return a + b."""
    return a + b


def sub(a: float, b: float) -> float:
    """Return a - b.

    Example:
        >>> sub(5, 3)
        2
    """
    return a - b


def mul(a: float, b: float) -> float:
    """Return a * b."""
    return a * b


def div(a: float, b: float) -> float:
    """Return a / b."""
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero")
    return a / b


# Name-to-function lookup table used by calculate().
OPERATIONS: Dict[str, Callable[[float, float], float]] = {
    "add": add,
    "sub": sub,
    "mul": mul,
    "div": div,
}


def calculate(op: str, a: float, b: float) -> float:
    """Look up `op` in OPERATIONS and apply it to a and b."""
    if op not in OPERATIONS:
        raise ValueError(f"Unknown operation: {op!r}")
    return OPERATIONS[op](a, b)


# A direct alias pointing at the same function object.
subtract_alias = sub


def subtotal(values):
    """Sum a list of numbers. Unrelated to sub() despite the shared prefix."""
    total = 0
    for v in values:
        total = add(total, v)
    return total


def subscribe(callback: Callable) -> None:
    """Pretend to register a callback. Also unrelated to sub()."""
    print(f"Subscribed: {callback.__name__}")


def run_demo() -> None:
    """Exercise every operation, including sub(), and print the results."""
    print("add(10, 4) =", add(10, 4))
    print("sub(10, 4) =", sub(10, 4))
    print("mul(10, 4) =", mul(10, 4))
    print("div(10, 4) =", div(10, 4))
    print("calculate('sub', 10, 4) =", calculate("sub", 10, 4))
    print("subtract_alias(10, 4) =", subtract_alias(10, 4))
    print("subtotal([1, 2, 3]) =", subtotal([1, 2, 3]))
    subscribe(sub)


if __name__ == "__main__":
    run_demo()