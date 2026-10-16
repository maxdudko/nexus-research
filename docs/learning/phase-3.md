# Phase 3 learning notes

This slice indexes a text PDF or one public HTML page and searches the accepted passages for a single `research_id`. `POST /api/v1/evidence/search` returns those passages. It does not call a chat model and it does not write an answer.

## Questions

### What are cosine similarity and top-k?

Each passage and the query are vectors from the same embedding model. Cosine similarity is the angle between two vectors: 1 means the same direction, 0 means orthogonal, and a negative value means opposite directions. Qdrant ranks points by that score and returns at most `top_k` of them. The default is 5, and the API allows 1 through 10.

The number is a measure of vector similarity. It is not a probability that the passage is factually correct, and the API does not turn it into a confidence label.

### Why does search use the accepted digest?

PostgreSQL records the last digest that finished indexing. Qdrant can still hold points from an attempt that lost the race or failed after writing. Those points use a different claim token, so they have different ids, and their `accepted` flag stays false.

Search loads source rows for the requested `research_id`, keeps the last `content_hash` for `INDEXED`, `FAILED`, and `INDEXING`, and asks Qdrant for points that match that pair and `accepted=true`. A failed replacement keeps the previous digest searchable. A digest that has not been accepted is left out. `PENDING` and a null hash are left out too.

`research_id` is part of that filter. It stops one research from receiving another research's passages. It does not check who the caller is. The API is not a multi-user authorization boundary.

### How is this evaluation different from answer quality?

`src/nexus/retrieval/data/retrieval_queries.json` has 5 short documents and 10 queries. Each query is labeled with the document and page that contain its terms. The evaluation hashes those tokens into a vector and ranks chunks with cosine similarity in Python. It does not call OpenAI.

Hit@5 is the fraction of queries whose labeled page appears in the top 5. Recall@5 is the fraction of that page's chunks appearing in the top 5. On this fixture both the production window (800 tokens, 120 overlap) and an alternate window (200 tokens, 40 overlap) score Hit@5 1.0 and Recall@5 1.0. Every page is one chunk at both sizes, and each query uses a token from the labeled page.

That result checks the fixture, the chunker, and the ranking arithmetic. It is a lexical baseline: the vectors are hashes of tokens, and Hit@5 1.0 is not a measurement of `text-embedding-3-small`. The only OpenAI embedding check is the opt-in live test, which checks that one query returns a finite vector of the configured size. It does not report Hit@5. Neither number says whether a later report quotes the right passage. Changing `NEXUS_CHUNK_TOKENS` does not rebuild an existing collection; a new chunk size needs a new `NEXUS_RAG_COLLECTION`.

## Limitations

- Original PDF bytes are not stored. A lost index is rebuilt by uploading the file again.
- Scanned pages, tables, and images are not extracted.
- Search is vector similarity over the accepted digest. There is no keyword index and no reranker.
- Default tests use a deterministic embedder. Paid embedding and public-page checks are opt-in: `NEXUS_RUN_LIVE_AI=1` and `NEXUS_RUN_LIVE_WEB=1` with `NEXUS_LIVE_WEB_URL`.
