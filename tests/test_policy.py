import unittest

from wslbuild.policy import aggregate, classify, lint_commit, proposed_version


class PolicyTests(unittest.TestCase):
    def test_docs_still_have_a_gate(self):
        self.assertEqual(classify(["README.md", "AGENTS.md"]), "docs")
        self.assertEqual(classify(["README.md"], uncertain=True), "product")
        self.assertEqual(classify([]), "product")

    def test_arbitrary_markdown_is_not_docs_only(self):
        for path in ["sources/input.md", "scripts/README.md", ".github/workflows/ci.yml",
                     "scripts/check", "sources/inputs.lock.json", "LICENSE", "NOTICE", "assets/kernel.c"]:
            self.assertEqual(classify([path]), "product")

    def test_acceptance_changes_are_isolated(self):
        self.assertEqual(classify(["acceptance/report.json"]), "acceptance")
        self.assertEqual(classify(["acceptance/report.json", "kernel/wsl.config"]), "product")

    def test_skip_tokens_in_subject_and_body(self):
        for token in ["[skip ci]", "[ci skip]", "[no ci]", "[skip actions]", "[actions skip]", "skip-checks: true"]:
            with self.subTest(token=token), self.assertRaises(ValueError):
                lint_commit("fix: reject skip\n\n" + token.upper())

    def test_conventional_subject(self):
        for subject in ["feat(kernel)!: change ABI", "docs: add usage", "fix(wsl): rollback"]:
            lint_commit(subject)
        for subject in ["updated stuff", "feat:", "fix(foo)?: bad"]:
            with self.assertRaises(ValueError):
                lint_commit(subject)

    def test_versions(self):
        self.assertEqual(proposed_version([], []), "0.1.0")
        tags = ["other-v99.0.0", "appsynergy-wsl-v0.1.0"]
        for commits, expected in [(["docs: explain recovery"], None), (["ci: add check"], None),
                                  (["fix: restore kernel"], "0.1.1"), (["perf: speed build"], "0.1.1"),
                                  (["feat: add feature"], "0.2.0"), (["feat!: change interface"], "1.0.0"),
                                  (["fix: change\n\nBREAKING CHANGE: incompatible"], "1.0.0")]:
            self.assertEqual(proposed_version(tags, commits), expected)

    def test_aggregate_rejects_missing_cancelled_and_unexpected_skip(self):
        expected = {"changes": True, "gates": True, "package-check": False}
        passing = {"changes": "success", "gates": "success", "package-check": "skipped"}
        aggregate(passing, expected)
        for status in ["failure", "cancelled", "skipped", ""]:
            with self.assertRaises(ValueError):
                aggregate(passing | {"gates": status}, expected)
        with self.assertRaises(ValueError):
            aggregate({"gates": "success"}, expected)
        with self.assertRaises(ValueError):
            aggregate(passing | {"package-check": "success"}, expected)
