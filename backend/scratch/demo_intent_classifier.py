import asyncio
import uuid
import sys
from pathlib import Path

# Add project root to python path to import backend modules correctly
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from backend.modules.vectordb.database import init_db, upsert_document, search_documents
from backend.modules.vectordb.embeddings import get_embedding

# Define sample intents and training phrases
SEED_INTENTS = [
    # Intent: get_weather
    ("what is the weather like today", "get_weather"),
    ("is it raining outside", "get_weather"),
    ("will it be sunny tomorrow in New York", "get_weather"),
    ("give me the current temperature", "get_weather"),
    # Intent: book_flight
    ("book a flight to Paris", "book_flight"),
    ("find me a ticket to London next Monday", "book_flight"),
    ("i need to fly to Tokyo", "book_flight"),
    ("cheapest flights to Berlin", "book_flight"),
    # Intent: play_music
    ("play some jazz music", "play_music"),
    ("put on my favorite playlist", "play_music"),
    ("start playing song by The Beatles", "play_music"),
    ("turn on the radio", "play_music"),
    # Intent: greet
    ("hello there", "greet"),
    ("hi, how are you", "greet"),
    ("good morning", "greet"),
    ("hey assistant", "greet"),
]

COLLECTION = "demo_intents"


async def setup_demo():
    print("Initializing Database...")
    init_db()

    print(f"Seeding '{COLLECTION}' collection with reference utterances...")
    for text, intent in SEED_INTENTS:
        # Generate embedding using active provider or fallback
        emb, source = await get_embedding(text)
        doc_id = f"intent_{uuid.uuid4().hex[:8]}"

        # Store training utterance with intent name in metadata
        upsert_document(
            doc_id=doc_id,
            collection=COLLECTION,
            text=text,
            metadata={"intent": intent, "source": source},
            embedding=emb,
        )
    print("Seeding complete!\n")


async def classify_intent(query: str, threshold: float = 0.25) -> dict:
    # 1. Get embedding for the user's query
    query_emb, source = await get_embedding(query)

    # 2. Search documents in the collection
    results = search_documents(COLLECTION, query_emb, limit=3)

    if not results:
        return {
            "query": query,
            "classified_intent": "unknown",
            "score": 0.0,
            "matches": [],
        }

    top_match = results[0]
    score = top_match["score"]
    classified_intent = top_match["metadata"].get("intent", "unknown")

    # Check threshold for out-of-domain queries
    if score < threshold:
        classified_intent = "unknown"

    return {
        "query": query,
        "classified_intent": classified_intent,
        "score": score,
        "top_match_text": top_match["text"],
        "embedding_source": source,
        "all_matches": [
            {
                "text": r["text"],
                "intent": r["metadata"].get("intent"),
                "score": r["score"],
            }
            for r in results
        ],
    }


async def main():
    await setup_demo()

    test_queries = [
        "Do I need an umbrella today?",
        "I want to fly to Paris tomorrow afternoon",
        "Could you play some rock music?",
        "Hey! Good afternoon",
        "What is the capital of France?",  # Should test threshold / out of domain
    ]

    print("--- Running Intent Classification Demo ---")
    for q in test_queries:
        res = await classify_intent(q)
        print(f"\nQuery: '{res['query']}'")
        print(
            f"Classified Intent: {res['classified_intent']} (Score: {res['score']:.4f})"
        )
        print(f"Top Matched Utterance: '{res['top_match_text']}'")
        print(f"Embedding Source: {res['embedding_source']}")
        print("Alternatives/Matches:")
        for m in res["all_matches"]:
            print(f"  - '{m['text']}' -> {m['intent']} (Score: {m['score']:.4f})")


if __name__ == "__main__":
    asyncio.run(main())
