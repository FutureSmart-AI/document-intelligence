from utils.qdrant.qdrant_utils import MultiQdrantDocumentIndexer, EMBEDDING_MODELS

# Pick an embedding model defined in EMBEDDING_MODELS
embedding_model = "paraphrase-multilingual-mpnet-base-v2"

indexer = MultiQdrantDocumentIndexer(embedding_model)
client = indexer.client
collection_name = indexer.collection_name

info = client.get_collection(collection_name)
print("config: ",info.config)
print("vectors: ",info.config.params.vectors)
print("payload_schema: ",info.payload_schema)