from packaging.version import Version


def add(a, b):
    return a + b


def newer(a, b):
    return Version(a) > Version(b)
