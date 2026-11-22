"""What one Python file says about risk (use_cases/rules/python_signals.py): ast metrics, write calls, TODOs."""
import unittest

from tests.domain.builders import sf

from clean_eval.use_cases.rules.python_signals import file_signals, python_stats, write_calls

SAMPLE = '''
def typed(a: int) -> int:
    if a and a > 1:
        return a
    return 0


def loose(x):
    try:
        return x
    except Exception:
        pass
'''


class PythonSignalsTest(unittest.TestCase):
    def test_python_stats(self):
        st = python_stats(SAMPLE)
        self.assertEqual((len(st.funcs), st.typed, st.broad, st.swallow), (2, 1, 1, 1))
        self.assertEqual(st.funcs[0][1], 3)  # one if and one boolean operator on top of the base path
        self.assertIsNone(python_stats("def broken(:"))

    def test_write_calls_are_counted_with_a_line_and_gated_by_context(self):
        aws = "import boto3\nc = boto3.client('s3')\nc.put_object(Bucket='b')\nc.delete_object(Bucket='b')\n"
        self.assertEqual(write_calls(aws)["aws_write"], (2, 3))
        self.assertNotIn("aws_write", write_calls("x.put_object(1)\n"))  # no boto import: not an AWS write

    def test_signals_are_cached_per_content(self):
        cache = {}
        a = file_signals(sf("a.py", 1, "# TODO a\nx = 1  # FIXME b\nTODOS = 3\n", blob="b1"), cache)
        self.assertEqual(a.todo, 2)
        self.assertIs(file_signals(sf("renamed.py", 1, "ignored", blob="b1"), cache), a)


if __name__ == "__main__":
    unittest.main()
