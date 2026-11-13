import io
import json
import os
import re
import tokenize
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.venv', 'node_modules', '__pycache__', 'data', 'runtime', 'models', '.git'}


def files():
    for directory, names, entries in os.walk(ROOT):
        names[:] = [name for name in names if name not in EXCLUDED]
        for entry in entries:
            yield Path(directory) / entry


class CommentCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.comments = []

    def handle_comment(self, data):
        self.comments.append(data)


def css_comments(tokens):
    result = []
    for token in tokens:
        if getattr(token, 'type', '') == 'comment':
            result.append(token)
        for field in ('content', 'arguments', 'prelude'):
            value = getattr(token, field, None)
            if isinstance(value, list):
                result.extend(css_comments(value))
    return result


class CommentsTests(unittest.TestCase):
    def test_code_has_no_comments(self):
        try:
            import tinycss2
            import tree_sitter_javascript
            from tree_sitter import Language, Parser
        except ImportError:
            self.skipTest('Установите requirements-test.txt для полного аудита комментариев')
        parser = Parser(Language(tree_sitter_javascript.language()))
        problems = []
        for path in files():
            suffix = path.suffix.lower()
            if suffix not in ('.py', '.js', '.mjs', '.html', '.css', '.json', '.bat', '.sh', '.yml', '.yaml', '.xml', '.gradle', '.java', '.properties'):
                continue
            text = path.read_text(encoding='utf-8')
            if suffix == '.py':
                for token in tokenize.generate_tokens(io.StringIO(text).readline):
                    if token.type == tokenize.COMMENT:
                        problems.append(f'{path.relative_to(ROOT)}:{token.start[0]} Python comment')
            elif suffix in ('.js', '.mjs'):
                tree = parser.parse(text.encode('utf-8'))
                nodes = [tree.root_node]
                while nodes:
                    node = nodes.pop()
                    if node.type == 'comment':
                        problems.append(f'{path.relative_to(ROOT)}:{node.start_point.row + 1} JavaScript comment')
                    nodes.extend(node.children)
            elif suffix in ('.html', '.xml'):
                collector = CommentCollector()
                collector.feed(text)
                if collector.comments:
                    problems.append(f'{path.relative_to(ROOT)} HTML comment')
            elif suffix == '.css':
                tokens = tinycss2.parse_stylesheet(text, skip_comments=False, skip_whitespace=False)
                if css_comments(tokens):
                    problems.append(f'{path.relative_to(ROOT)} CSS comment')
            elif suffix == '.json':
                json.loads(text)
            elif suffix == '.sh':
                for number, line in enumerate(text.splitlines(), 1):
                    if re.match(r'^\s*#', line) and not (number == 1 and line.startswith('#!')):
                        problems.append(f'{path.relative_to(ROOT)}:{number} shell comment')
            elif suffix == '.bat':
                for number, line in enumerate(text.splitlines(), 1):
                    if re.match(r'^\s*(?:@?rem(?:\s|$)|::)', line, re.I):
                        problems.append(f'{path.relative_to(ROOT)}:{number} batch comment')
            elif suffix in ('.gradle', '.java'):
                for number, line in enumerate(text.splitlines(), 1):
                    if re.match(r'^\s*(?://|/\*|\*)', line):
                        problems.append(f'{path.relative_to(ROOT)}:{number} code comment')
            else:
                for number, line in enumerate(text.splitlines(), 1):
                    if re.match(r'^\s*#', line):
                        problems.append(f'{path.relative_to(ROOT)}:{number} comment')
        self.assertEqual(problems, [])


if __name__ == '__main__':
    unittest.main()
