"""Regression checks for the source-to-site/Wiki publication boundary."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare_docs


class DocumentationGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / "docs"
        for language in ("wiki", "en"):
            (self.docs / language).mkdir(parents=True)
        self.pages = [{"slug": "index", "zh": "Home"}, {"slug": "guide", "zh": "指南"}]
        for name in ["wiki/Home.md", "en/index.md"]:
            (self.docs / name).write_text(
                "# Home\n\n[Guide](指南.md#章节)\n"
                if name.startswith("wiki")
                else "# Home\n\n[Guide](guide.md#section)\n"
            )
        (self.docs / "wiki/指南.md").write_text(
            "# 指南\n\n## 章节\n\n```text\n[example](missing.md)\n```\n"
        )
        (self.docs / "en/guide.md").write_text(
            "# Guide\n\n## Section\n\n```text\n[example](missing.md)\n```\n"
        )
        self.patch_docs = patch.object(prepare_docs, "DOCS", self.docs)
        self.patch_pages = patch.object(prepare_docs, "PAGES", self.pages)
        self.patch_docs.start()
        self.patch_pages.start()
        self.addCleanup(self.patch_docs.stop)
        self.addCleanup(self.patch_pages.stop)

    def generate(self, target="site", output=None):
        prepare_docs.generate(
            target,
            output or self.root / target,
            "https://docs.example/Afterglow/",
            "https://github.example/project/wiki",
        )

    def test_paired_sections_and_fenced_examples(self):
        self.generate()
        home = (self.root / "site/zh/index.md").read_text()
        self.assertIn("[Guide](guide.md#section-1)", home)
        for language in ("zh", "en"):
            guide = (self.root / f"site/{language}/guide.md").read_text()
            self.assertIn("{#section-1}", guide)
            self.assertIn("[example](missing.md)", guide)

    def test_wiki_preserves_chinese_names_and_adds_english_mirror(self):
        self.generate("wiki")
        self.assertTrue((self.root / "wiki/指南.md").is_file())
        self.assertTrue((self.root / "wiki/EN-guide.md").is_file())
        self.assertIn(
            "https://docs.example/Afterglow/en/guide.html",
            (self.root / "wiki/EN-guide.md").read_text(),
        )
        self.assertIn(
            "/wiki/EN-guide#section", (self.root / "wiki/EN-index.md").read_text()
        )

    def test_missing_counterpart_and_missing_anchor_are_rejected(self):
        english = self.docs / "en/guide.md"
        original = english.read_text()
        english.unlink()
        with self.assertRaises(ValueError):
            self.generate()
        english.write_text(original)
        (self.docs / "en/index.md").write_text(
            "# Home\n\n[Guide](guide.md#nonexistent)\n"
        )
        with self.assertRaisesRegex(ValueError, "missing anchor"):
            self.generate()

    def test_mismatched_sections_are_rejected(self):
        (self.docs / "en/index.md").write_text("# Home\n\n[Guide](guide.md)\n")
        (self.docs / "en/guide.md").write_text("# Guide\n")
        with self.assertRaisesRegex(ValueError, "section counts differ"):
            self.generate()

    def test_source_outputs_and_symlinks_are_rejected(self):
        for output in [self.docs, self.docs / "en", self.docs / "wiki/nested"]:
            with self.assertRaisesRegex(ValueError, "source directory"):
                self.generate(output=output)
        original = self.docs / "en/guide.md"
        outside = self.root / "outside.md"
        original.rename(outside)
        original.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.generate()


if __name__ == "__main__":
    unittest.main()
