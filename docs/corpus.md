# Textbook corpus

The RAG demo is built on 13 source PDFs (`hesc*.pdf`, `hess*.pdf`). They are
**not distributed with this repository** because they are third-party
textbooks. They are listed in `.gitignore` and live only on the operator's
machine, at the repository root:

```
lms/
  hesc101.pdf ... hesc113.pdf
  hess201.pdf hess202.pdf hess301.pdf
```

This does not change how retrieval works. Ingestion reads the PDFs from disk,
stores extracted text and chunk rows in Postgres, and writes vectors to Chroma.
Once a document has been ingested, the assistant continues to answer from
Postgres and Chroma even if the source PDF is moved—as long as the
`lms_uploads` and `lms_chromadata` volumes are intact.

## Fresh clone or new machine

1. Obtain the corresponding textbook PDFs from their official source.
2. Put all 13 files in the repository root (or pass `--dir` to the checker).
3. Verify them:

   ```powershell
   python scripts/verify_corpus.py
   ```

   The checker uses SHA-256 and byte sizes from
   `scripts/verify_corpus.py:MANIFEST`, so a truncated or substituted file is
   reported instead of silently degrading retrieval.

4. Start the stack and ingest as usual:

   ```powershell
   docker compose --env-file .env -f infra/docker-compose.yml up -d --build
   docker compose --env-file .env -f infra/docker-compose.yml exec -T api python -m app.seed
   backend\.venv\Scripts\python.exe scripts\demo_seed.py
   ```

`demo_seed.py` already fails fast with a `missing PDFs` message when the corpus
is absent; the verifier adds the integrity check.

## What is safe to commit

| Path | Committed? | Why |
|---|---|---|
| `hesc*.pdf`, `hess*.pdf` (root) | No | Copyrighted source corpus |
| `web/e2e/fixtures/demo.pdf` | Yes | Small generated fixture, not a textbook |
| `data/` | No | Regenerable inspection exports (`scripts/export_chunks.py`) |
| `backups/` | No | Postgres dumps can contain credentials and user rows |
| `load/sessions.json` | No | Contains live JWT cookies from load-test logins |
| `.env` | No | Secrets |

The `data/README.md` file and the export script remain in git, so operators can
regenerate the chunk/embedding dump locally without publishing textbook text.

## If the manifest changes

Only update `MANIFEST` after re-ingesting the affected document. Hashing proves
the file is the same one the evaluation baseline used; a new edition can change
chunking and therefore retrieval scores. Run the RAG evaluation after any
corpus change:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec -T api python -m app.cli rag-eval
```
