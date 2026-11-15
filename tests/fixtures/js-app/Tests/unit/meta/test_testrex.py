"""The meta suite, which runs eslint over greet.js and the test file."""

from pytest_testrex import suite


class TestRex(suite.TestRex):
    """The suite's own rules, from pytest-testrex."""
