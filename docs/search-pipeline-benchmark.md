# Paired full-catalog search benchmark

`scripts/benchmark_search_pipelines.py` is the canonical benchmark for search
quality and warm latency. It runs every selected pipeline against the same
held-out queries, complete ParadeDB catalog, and relevance judgments.

The default pipelines are:

- BM25;
- semantic retrieval, including query embedding;
- BM25/semantic Reciprocal Rank Fusion;
- RRF with the committed production LTR model;
- RRF with the cached cross-encoder model.

The JSON report pairs each query's ranking metrics with repeated latency
samples and also contains aggregate mean, p50, p95, p99, stage timings, and
paired deltas against the selected baseline. A human-readable Markdown report
is written beside it.

## Methodology

- The production LTR model is loaded but never retrained or replaced.
- The fixed held-out split is required by default, preventing evaluation on
  queries used to train the production model.
- All pipelines search the full catalog and retain 250 candidates by default.
- Unjudged results retain their real rank and count as nonrelevant. Judgment
  coverage at 10 and 25 is reported to expose incomplete pooling.
- Model and database warm-up runs are excluded.
- Each query is measured three times by default.
- Pipeline execution order rotates across queries and repetitions to reduce
  systematic cache-order bias.
- Quality uses NDCG@10, MRR@25, MAP@25, recall@25, and hit rate@25.

This is a controlled single-client latency benchmark, not a concurrency or
capacity test. Run it on a production-sized database snapshot for repeatable
release comparisons. Running the cross-encoder pipeline on the live server is
CPU-intensive.

## Run inside the app container

The ignored annotation and split artifacts are not baked into the application
image. Copy the benchmark inputs into the running container:

```bash
docker compose cp artifacts/human-ltr-annotations-2026-07-26-2.json \
  app:/tmp/search-annotations.json
docker compose cp artifacts/ltr/full-catalog-query-split-updated.json \
  app:/tmp/search-query-split.json
```

Run the complete benchmark:

```bash
docker compose exec app python scripts/benchmark_search_pipelines.py \
  --annotations /tmp/search-annotations.json \
  --split-file /tmp/search-query-split.json \
  --output /tmp/search-pipeline-benchmark.json
```

Copy the durable report back to the host:

```bash
docker compose cp \
  app:/tmp/search-pipeline-benchmark.json \
  artifacts/search-pipeline-benchmark.json
docker compose cp \
  app:/tmp/search-pipeline-benchmark.md \
  artifacts/search-pipeline-benchmark.md
```

For a fast smoke run:

```bash
docker compose exec app python scripts/benchmark_search_pipelines.py \
  --annotations /tmp/search-annotations.json \
  --split-file /tmp/search-query-split.json \
  --pipelines bm25,semantic,rrf,rrf_ltr \
  --query-limit 3 \
  --repetitions 1 \
  --warmup-queries 1 \
  --output /tmp/search-pipeline-benchmark-smoke.json
```

Use `--all-queries` only for diagnostics. Those results can include queries
used to train the production LTR model and should not be reported as held-out
quality.

## Cross-encoder backend experiments

The canonical benchmark can compare the normal PyTorch cross-encoder with an
ONNX export. Install the optional backend with
`pip install "sentence-transformers[onnx]"`, then pass
`--cross-encoder-backend onnx`. Use `--cross-encoder-model-file` to select a
specific ONNX file, and tune inference with `--cross-encoder-max-length` and
`--cross-encoder-batch-size`. These options affect benchmark execution only;
they do not change the production model configuration.
