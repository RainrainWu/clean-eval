"""What one Python file says about risk (use_cases/rules/python_signals.py): ast metrics, write calls, TODOs."""

from clean_eval.use_cases.rules.python_signals import file_signals, python_stats, write_calls
from tests.domain.builders import sf

SAMPLE = """
def typed(a: int) -> int:
    if a and a > 1:
        return a
    return 0


def loose(x):
    try:
        return x
    except Exception:
        pass
"""


def test_python_stats():
    st = python_stats(SAMPLE)
    assert (len(st.funcs), st.typed, st.broad, st.swallow) == (2, 1, 1, 1)
    assert st.funcs[0][1] == 3  # one if and one boolean operator on top of the base path
    assert python_stats("def broken(:") is None


def test_write_calls_are_counted_with_a_line_and_gated_by_context():
    aws = "import boto3\nc = boto3.client('s3')\nc.put_object(Bucket='b')\nc.delete_object(Bucket='b')\n"
    assert write_calls(aws)["aws_write"] == (2, 3)
    assert "aws_write" not in write_calls("x.put_object(1)\n")  # no boto import: not an AWS write


def test_signals_are_cached_per_content():
    cache = {}
    a = file_signals(sf("a.py", 1, "# TODO a\nx = 1  # FIXME b\nTODOS = 3\n", blob="b1"), cache)
    assert a.todo == 2
    assert file_signals(sf("renamed.py", 1, "ignored", blob="b1"), cache) is a
