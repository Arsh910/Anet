from typing import List, Union


def average(numbers: List[Union[int, float]]) -> Union[int, float]:
    """
    Calculate the average of a list of numbers.
    
    Args:
        numbers: A list of integers or floats.
    
    Returns:
        The average value of the numbers in the list.
    
    Raises:
        ValueError: If the list is empty.
    """
    if not numbers:
        raise ValueError("Cannot calculate average of an empty list")
    
    return sum(numbers) / len(numbers)
