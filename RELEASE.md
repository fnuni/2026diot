# Reproducibility release checklist

Validate the intended release content before tagging or archiving it.

~~~sh
python -m aedge.release_integrity
python -m pytest -q tests
python scripts/check_analyses.py
python scripts/check_replay.py
~~~

Include code, configurations, analysis descriptions, all run tables, verification records, sensitivities, reported-seed raw logs, licensing and citation metadata. Exclude manuscript source, cover letters, author notes, development history and regenerated full-log working directories.

CSV hashes are LF-normalised and .gitignore is excluded from the manifest. Validate a fresh Git checkout, not only the source working directory.

After selecting immutable release content, create its version tag, archive that exact release with a suitable repository such as Zenodo and add the real version DOI to CITATION.cff and the article's data statement. Do not invent an identifier. Software is MIT licensed; data/documentation are CC BY 4.0.
