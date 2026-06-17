"""Module for calculating the average of a list of numbers."""

from typing import List, Union


def average(numbers: List[Union[int, float]]) -> float:
    """
    Calculate the average of a list of numbers.
    
    Args:
        numbers: A list of integers or floats.
        
    Returns:
        The average value as a float.
        
    Raises:
        ValueError: If the list is empty.
    """
    if not numbers:
        raise ValueError("Cannot calculate average of an empty list")
    
    total: float = sum(numbers)
    count: int = len(numbers)
    result: float = total / count
    
    return result
