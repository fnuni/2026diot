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

Source repository: [https://github.com/fnuni/2026diot](https://github.com/fnuni/2026diot).

After validating the final content, upload it to the source repository and create a versioned release. Attach the matching reproducibility archive. Software is MIT licensed; data/documentation are CC BY 4.0.
