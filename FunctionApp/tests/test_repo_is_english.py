"""This repository is public. Nothing in it is written in Turkish.

The project was built in Turkish and the prose leaked in three ways, each one
invisible to the check that would have caught the previous one:

  1. Turkish letters (ğ ş ı İ) — found by a character scan.
  2. Turkish with the accents stripped: "GOSTER", "SESSIZLIK != BASARI",
     "deaktif user'lari". A character scan reports zero on these, so a clean
     scan was read as a clean repo.
  3. A single Turkish word sitting where it looked like English: `standart`,
     which was not a comment but the DEFAULT VALUE of a template parameter and
     an enum in the code. Spell-checking prose would never have reached it.

So this file checks the bytes, the words, and the values a customer is offered.
A grep that finds nothing is not evidence of absence — it is evidence about
where you looked.
"""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[2]

# Letters that exist in Turkish and not in English. Decisive on their own.
TURKISH_LETTERS = set("ğĞşŞıİ")

# Accent-stripped Turkish that cannot be mistaken for an English word. Two
# rules learned the hard way:
#   - match the STEM plus any suffix. Turkish agglutinates, so a \b-anchored
#     "degisiklik" misses "Degisiklikleri" — which is exactly what slipped
#     through the first version of this check, in a portal form label.
#   - leave out anything that collides with English. "var" is a bicep keyword,
#     "bu" occurs inside hashes, "sil" is the start of "silence".
TURKISH_STEMS = [
    "goster", "sessizlik", "basari", "deaktif", "standart",
    "onceki", "sonraki", "simdiki", "cunku", "yanlis", "hicbir", "birkac",
    "degisiklik", "guncelle", "uygula", "kaydet", "baslat", "durdur", "kapat",
    # "indir" would match "indirect" and "indirection"; the -me form does not.
    "olustur", "duzenle", "yukle", "indirme", "kullanici", "kullanilir",
    "kurulum", "calisma", "yapilir", "edilir", "olmali", "gerekir",
    "yazilir", "silinir", "eklenir", "dosya", "satir", "hata", "kural",
    "kaynak", "surum", "kosu", "ornek", "deneme", "ayarlar", "sayfa",
]
TURKISH_WORDS = re.compile(
    r"\b(" + "|".join(TURKISH_STEMS) + r")\w*\b", re.IGNORECASE
)

# The one place the legacy Turkish spelling is still allowed to appear: the
# compatibility shim that keeps already-running installations working, and the
# test that proves the shim works. Anything else is a leak.
LEGACY_SPELLING_ALLOWED = {
    "FunctionApp/utils/config.py",
    "FunctionApp/actions/former_companies.py",
    "FunctionApp/tests/test_config_warnings.py",
    "FunctionApp/tests/test_repo_is_english.py",
    "deploy/main.bicep",
}


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=REPO,
                         capture_output=True, text=True).stdout
    return [f for f in out.split() if f]


def readable(path):
    try:
        return (REPO / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return None


class NoTurkishLetters(unittest.TestCase):

    def test_not_one_tracked_file_contains_them(self):
        hits = []
        for f in tracked_files():
            text = readable(f)
            if text is None:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if TURKISH_LETTERS & set(line):
                    hits.append(f"{f}:{n}: {line.strip()[:70]}")
        self.assertEqual(hits, [], "Turkish letters in a public repo:\n" + "\n".join(hits))

    def test_file_and_directory_names_are_english(self):
        bad = [f for f in tracked_files()
               if TURKISH_LETTERS & set(f) or TURKISH_WORDS.search(f)]
        self.assertEqual(bad, [])


class NoAccentStrippedTurkish(unittest.TestCase):
    """The class of leak a character scan cannot see."""

    def test_no_tracked_file_carries_a_turkish_word(self):
        hits = []
        for f in tracked_files():
            text = readable(f)
            if text is None:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                found = TURKISH_WORDS.findall(line)
                if not found:
                    continue
                if f in LEGACY_SPELLING_ALLOWED and set(found) <= {"standart"}:
                    continue  # the compatibility shim, deliberately kept
                hits.append(f"{f}:{n}: {found} :: {line.strip()[:60]}")
        self.assertEqual(hits, [], "Turkish written without its accents:\n" + "\n".join(hits))


class NothingTurkishIsOfferedToACustomer(unittest.TestCase):
    """Values, not prose — the leak that hid inside a parameter default."""

    def setUp(self):
        self.arm = json.loads((REPO / "deploy" / "azuredeploy.json").read_text(encoding="utf-8"))
        self.ui = json.loads((REPO / "deploy" / "createUiDefinition.json").read_text(encoding="utf-8"))

    def _strings(self, node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for v in node.values():
                yield from self._strings(v)
        elif isinstance(node, list):
            for v in node:
                yield from self._strings(v)

    def test_no_parameter_default_or_allowed_value_is_turkish(self):
        bad = []
        for name, spec in self.arm.get("parameters", {}).items():
            for value in [spec.get("defaultValue")] + list(spec.get("allowedValues") or []):
                if isinstance(value, str) and TURKISH_WORDS.search(value):
                    bad.append(f"{name}: {value!r}")
        self.assertEqual(bad, [], "a customer would be handed a Turkish value: " + str(bad))

    def test_the_ruleset_mode_spelling_is_english(self):
        p = self.arm["parameters"]["RulesetMode"]
        self.assertEqual(p["allowedValues"], ["standard", "strict"])
        self.assertEqual(p["defaultValue"], "standard")

    def test_the_portal_form_has_no_turkish(self):
        bad = [s[:70] for s in self._strings(self.ui)
               if TURKISH_LETTERS & set(s) or TURKISH_WORDS.search(s)]
        self.assertEqual(bad, [], "the form a customer fills in: " + str(bad))

    def test_the_template_descriptions_have_no_turkish(self):
        bad = [s[:70] for s in self._strings(self.arm.get("parameters", {}))
               if TURKISH_LETTERS & set(s) or TURKISH_WORDS.search(s)]
        self.assertEqual(bad, [])


class TheReadmeDoesNotClaimAnythingIsTurkish(unittest.TestCase):
    """It said a linked doc was 'written in Turkish' — true when written,
    false the moment the doc was translated, and on the front page."""

    def test_it_makes_no_language_claim(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("in Turkish", readme)
        self.assertNotIn("Turkish", readme)


if __name__ == "__main__":
    unittest.main()
