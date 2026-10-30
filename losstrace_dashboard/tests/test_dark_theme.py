"""Dependency-free checks for the UI-only dark release; no subprocess/model/database calls."""
import ast
from pathlib import Path
import unittest

UI = Path(__file__).resolve().parents[1]


class DarkThemeReleaseTests(unittest.TestCase):
    def test_dark_streamlit_launcher_and_local_binding(self):
        launcher = (UI / 'launch.ps1').read_text(encoding='utf-8')
        self.assertIn('"--theme.base=dark"', launcher)
        self.assertIn('"--server.address=127.0.0.1"', launcher)
        self.assertIn('"--theme.primaryColor=#3BE0CC"', launcher)
        self.assertIn('uv run --frozen', launcher)

    def test_dark_palette_readability_and_outcome_layout(self):
        css = (UI / 'styles.css').read_text(encoding='utf-8')
        self.assertIn('--lt-bg: #071324', css)
        self.assertIn('--lt-accent: #3be0cc', css)
        self.assertIn('.lt-handoff', css)
        self.assertIn('.lt-claim-details', css)
        self.assertIn('.lt-check span { display:block; width:auto;', css)
        self.assertNotIn(':root { color-scheme: light;', css)

    def test_only_presentation_functions_added(self):
        app = (UI / 'app.py').read_text(encoding='utf-8')
        ast.parse(app)
        ast.parse((UI / 'investigation.py').read_text(encoding='utf-8'))
        self.assertIn('def show_decision_banner(snapshot: dict):', app)
        self.assertIn('def show_overview_sources_and_audit(snapshot: dict):', app)
        self.assertIn('show_decision_banner(snapshot)', app)
        self.assertIn('except (TypeError, ImportError):', app)
        self.assertIn('except ImportError:', app)
        self.assertIn('read_snapshot(finance_path, dossier)', app)

    def test_status_banner_uses_saved_record(self):
        app = ast.parse((UI / 'app.py').read_text(encoding='utf-8'))
        banner = next(node for node in app.body if isinstance(node, ast.FunctionDef)
                      and node.name == 'show_decision_banner')
        text = ast.get_source_segment((UI / 'app.py').read_text(encoding='utf-8'), banner)
        self.assertIn('snapshot["record"]', text)
        self.assertIn('snapshot["financial"]', text)
        self.assertNotIn('12105', text)
        self.assertNotIn('876731', text)


if __name__ == '__main__':
    unittest.main()
