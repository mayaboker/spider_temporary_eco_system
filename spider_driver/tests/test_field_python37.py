import ast
from pathlib import Path
import sys
import unittest


FIELD_FILES = (
    "main.py",
    "field_process.py",
    "spider_logic.py",
    "liveness_loop.py",
    "definitions.py",
    "teensy_protocol.py",
)


class FieldPython37CompatibilityTests(unittest.TestCase):
    def test_field_import_graph_parses_with_python37_grammar(self):
        root = Path(__file__).resolve().parents[1]
        for relative_path in FIELD_FILES:
            source = (root / relative_path).read_text(encoding="utf-8")
            with self.subTest(file=relative_path):
                if sys.version_info >= (3, 8):
                    ast.parse(source, filename=relative_path, feature_version=7)
                else:
                    # Running this test under 3.7 is itself the grammar check.
                    ast.parse(source, filename=relative_path)


if __name__ == "__main__":
    unittest.main()
