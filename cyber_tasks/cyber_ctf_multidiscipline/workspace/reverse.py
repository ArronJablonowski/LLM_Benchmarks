DATA = [67, 73, 68, 66, 126, 119, 96, 115, 96, 119, 118, 96, 90, 105, 106, 98, 108, 102, 120]
print("".join(chr(value ^ 5) for value in DATA))
