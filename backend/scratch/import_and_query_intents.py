import asyncio
import sys
import uuid
from pathlib import Path
import httpx
import yaml

# Add project root to python path to import backend modules correctly
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from backend.modules.database.vectorstore import (
    init_db,
    upsert_document,
    search_documents,
    get_db_stats,
)
from backend.modules.database.embeddings import get_embedding

COLLECTION = "yaml_intents"


async def import_yaml(source: str):
    print(f"Fetching YAML from: {source}")
    try:
        if source.startswith("http://") or source.startswith("https://"):
            # Map GitHub blob URLs to raw content URLs if user pasted the browser URL
            if "github.com" in source and "/blob/" in source:
                source = source.replace(
                    "github.com", "raw.githubusercontent.com"
                ).replace("/blob/", "/")
                print(f"Converted GitHub blob URL to raw URL: {source}")

            async with httpx.AsyncClient() as client:
                res = await client.get(source)
                res.raise_for_status()
                yaml_text = res.text
        else:
            with open(source, "r", encoding="utf-8") as f:
                yaml_text = f.read()
    except Exception as e:
        print(f"Error reading YAML source: {e}")
        return

    try:
        data = yaml.safe_load(yaml_text)
    except Exception as e:
        print(f"Error parsing YAML: {e}")
        return

    if not data or "intents" not in data:
        print("Invalid YAML structure: Root must contain an 'intents' key.")
        return

    init_db()
    intents_dict = data["intents"]

    # We will gather all utterances to embed them
    utterances_to_embed = []

    for namespace, namespace_data in intents_dict.items():
        if not isinstance(namespace_data, dict):
            continue
        for intent_name, intent_data in namespace_data.items():
            if not isinstance(intent_data, dict):
                continue
            utterances = intent_data.get("utterances", [])
            for utterance in utterances:
                if isinstance(utterance, str):
                    utterances_to_embed.append((utterance, namespace, intent_name))

    print(
        f"Found {len(utterances_to_embed)} utterances to embed and store in collection '{COLLECTION}'."
    )

    count = 0
    for text, namespace, intent in utterances_to_embed:
        print(f"Embedding [{count + 1}/{len(utterances_to_embed)}]: '{text}'")
        try:
            emb, source_model = await get_embedding(text)
            doc_id = f"yaml_{uuid.uuid4().hex[:8]}"
            upsert_document(
                doc_id=doc_id,
                collection=COLLECTION,
                text=text,
                metadata={
                    "namespace": namespace,
                    "intent": intent,
                    "full_intent": f"{namespace}/{intent}",
                    "embedding_source": source_model,
                },
                embedding=emb,
            )
            count += 1
        except Exception as e:
            print(f"  Failed to embed '{text}': {e}")

    print(f"\nSuccessfully imported {count} utterances into collection '{COLLECTION}'!")

    # Print stats
    stats = get_db_stats()
    print(f"Current Vector DB total documents: {stats['num_documents']}")


async def query_intent(query_text: str, limit: int = 5, threshold: float = 0.5):
    print(f"Querying: '{query_text}'")
    try:
        emb, source_model = await get_embedding(query_text)
    except Exception as e:
        print(f"Failed to generate embedding: {e}")
        return None

    results = search_documents(COLLECTION, emb, limit=limit)
    if not results:
        print("No matches found in collection.")
        return None

    # Group by intent, taking the highest score (max pooling) for each unique intent
    intent_scores = {}
    for r in results:
        meta = r["metadata"]
        ns = meta.get("namespace", "unknown")
        intent = meta.get("intent", "unknown")
        full_intent = f"{ns}/{intent}"
        score = r["score"]
        if full_intent not in intent_scores or score > intent_scores[full_intent]:
            intent_scores[full_intent] = score

    # Sort intents by score descending
    sorted_intents = sorted(intent_scores.items(), key=lambda x: x[1], reverse=True)

    print(f"Classified Intents (using embedding: {source_model}):")
    print("-" * 60)
    for i, (intent, score) in enumerate(sorted_intents):
        star = "* " if i == 0 and score >= threshold else "  "
        print(f"{star}{intent}: {score:.4f}")
    print("-" * 60)

    top_intent, top_score = sorted_intents[0]
    if top_score >= threshold:
        decision = (top_intent, top_score)
    else:
        decision = ("unknown", top_score)

    print(f"Decision: {decision}")
    return decision


async def main():
    if len(sys.argv) == 1:
        # Default behavior: run a full demo!
        default_url = "https://raw.githubusercontent.com/ConstCorrectness/DistanceMetrics/main/intents.yaml"
        print("Running full end-to-end demo...")
        await import_yaml(default_url)

        test_queries = [
            "I want to see my wishlist",
            "Can I request a small loan from a friend?",
            "Let's avoid those bank fees",
            "I want to buy a present for my mom",
            "What events are coming up next month?",
            "I am feeling very hungry",
            "Can you tell me a joke?",  # Out of domain
        ]
        print("\n=== Running test queries ===")
        for q in test_queries:
            await query_intent(q)
            print()
        return

    cmd = sys.argv[1].lower()
    if cmd == "import":
        source = (
            sys.argv[2]
            if len(sys.argv) > 2
            else "https://raw.githubusercontent.com/ConstCorrectness/DistanceMetrics/main/intents.yaml"
        )
        await import_yaml(source)
    elif cmd == "query":
        if len(sys.argv) < 3:
            print("Please provide a query string.")
            return
        query_text = sys.argv[2]
        await query_intent(query_text)
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    asyncio.run(main())
