import io
import json
import os
import re
import tokenize
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOT = ROOT.parent if (ROOT.parent / '.git').is_dir() and (ROOT.parent / 'python_studio').is_dir() else ROOT
EXCLUDED = {'.venv', 'node_modules', '__pycache__', 'data', 'runtime', 'models', '.git', '.gradle', '.vite', 'dist', 'build', 'target', 'capacitor-cordova-android-plugins'}


def files():
    for directory, names, entries in os.walk(SCAN_ROOT):
        names[:] = [name for name in names if name not in EXCLUDED]
        for entry in entries:
            yield Path(directory) / entry


class CommentCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.comments = []
        self.scripts = []
        self.styles = []
        self.active = None
        self.chunks = []

    def handle_comment(self, data):
        self.comments.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == 'style' or tag == 'script' and dict(attrs).get('type', '').lower() not in ('application/json', 'application/ld+json'):
            self.active = tag
            self.chunks = []

    def handle_data(self, data):
        if self.active:
            self.chunks.append(data)

    def handle_endtag(self, tag):
        if tag == self.active:
            (self.scripts if tag == 'script' else self.styles).append(''.join(self.chunks))
            self.active = None
            self.chunks = []


def hash_comment(line):
    quote = ''
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif character == '\\' and quote != "'":
            escaped = True
        elif quote and character == quote:
            quote = ''
        elif not quote and character in ("'", '"'):
            quote = character
        elif not quote and character == '#' and (index == 0 or line[index - 1].isspace()):
            return True
    return False


def javascript_comments(parser, text):
    nodes = [parser.parse(text.encode('utf-8')).root_node]
    found = []
    while nodes:
        node = nodes.pop()
        if node.type == 'comment':
            found.append(node.start_point.row + 1)
        else:
            nodes.extend(node.children)
    return found


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
        self.maxDiff = None
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
                        problems.append(f'{path.relative_to(SCAN_ROOT)}:{token.start[0]} Python comment')
            elif suffix in ('.js', '.mjs'):
                for number in javascript_comments(parser, text):
                    problems.append(f'{path.relative_to(SCAN_ROOT)}:{number} JavaScript comment')
            elif suffix in ('.html', '.xml'):
                collector = CommentCollector()
                collector.feed(text)
                if collector.comments:
                    problems.append(f'{path.relative_to(SCAN_ROOT)} HTML comment')
                for script in collector.scripts:
                    if javascript_comments(parser, script):
                        problems.append(f'{path.relative_to(SCAN_ROOT)} embedded JavaScript comment')
                for style in collector.styles:
                    if css_comments(tinycss2.parse_stylesheet(style, skip_comments=False, skip_whitespace=False)):
                        problems.append(f'{path.relative_to(SCAN_ROOT)} embedded CSS comment')
            elif suffix == '.css':
                tokens = tinycss2.parse_stylesheet(text, skip_comments=False, skip_whitespace=False)
                if css_comments(tokens):
                    problems.append(f'{path.relative_to(SCAN_ROOT)} CSS comment')
            elif suffix == '.json':
                json.loads(text)
            elif suffix == '.sh':
                for number, line in enumerate(text.splitlines(), 1):
                    if hash_comment(line) and not (number == 1 and line in ('#!/bin/sh', '#!/usr/bin/env bash')):
                        problems.append(f'{path.relative_to(SCAN_ROOT)}:{number} shell comment')
            elif suffix == '.bat':
                for number, line in enumerate(text.splitlines(), 1):
                    if re.search(r'(?:^\s*|[&|]\s*)@?rem(?:\s|$)|^\s*::', line, re.I):
                        problems.append(f'{path.relative_to(SCAN_ROOT)}:{number} batch comment')
            elif suffix in ('.gradle', '.java'):
                for number, line in enumerate(text.splitlines(), 1):
                    if re.match(r'^\s*(?://|/\*|\*)', line):
                        problems.append(f'{path.relative_to(SCAN_ROOT)}:{number} code comment')
            else:
                for number, line in enumerate(text.splitlines(), 1):
                    if hash_comment(line):
                        problems.append(f'{path.relative_to(SCAN_ROOT)}:{number} comment')
        self.assertEqual(problems, [])


if __name__ == '__main__':
    unittest.main()
