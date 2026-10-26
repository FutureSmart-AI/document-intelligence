# Grounded answer control

`grounded-answer.json` describes the small original fixture used by the service
example and full-product installation check. Run the existing product's pipeline:

```sh
python examples/rag_service.py --env-file .env
```

The example creates a collection, uploads the file, waits for asynchronous
completion, retrieves the selected source and calls product-owned chat. It
checks both facts and the returned source text/ID. It works with the UI stopped.
It does not implement another RAG pipeline or measure general answer accuracy.

Hard retrieval-scope regressions live in `tests/unit/test_search_scope.py` and
run against an isolated Qdrant server. Historical experiments and private
benchmark outputs are kept outside this product repository.
