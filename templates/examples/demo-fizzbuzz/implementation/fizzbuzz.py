"""DEMO-001 的 implementer 產出：通用邏輯，不查表，適用任意非負整數 n。"""


def fizzbuzz(n: int) -> list:
    if n < 0:
        raise ValueError("n 不可為負數")

    result = []
    for i in range(1, n + 1):
        if i % 15 == 0:
            result.append("FizzBuzz")
        elif i % 3 == 0:
            result.append("Fizz")
        elif i % 5 == 0:
            result.append("Buzz")
        else:
            result.append(str(i))
    return result
